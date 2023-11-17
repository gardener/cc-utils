import logging
import typing
import datetime
import enum

import gci.componentmodel
import git
import github3.repos

import cnudie.retrieve
import cnudie.util
import gitutil
import github.util
import release_notes.model as rnm
import release_notes.utils as rnu
import version

logger = logging.getLogger(__name__)


class SpecialVersion(enum.Enum):
    HEAD = enum.auto()
    INITIAL = enum.auto()


def _list_commits_since_tag(
        repo: git.Repo,
        tag: git.TagReference,
) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
    '''Return a list of between the given tag and HEAD

    :return: a tuple of commits'''
    if repo.is_ancestor(tag.commit, 'HEAD'):
        logger.info(f"Commit tagged '{tag.name}' is a direct ancestor of HEAD")
        return tuple(repo.iter_commits(f'HEAD...{tag.commit.hexsha}')), tuple()

    if (
        not (merge_commit_list := repo.merge_base('HEAD', tag))
        or not (merge_commit := merge_commit_list.pop())
    ):
        raise RuntimeError('cannot find merge base')
    return (
        tuple(repo.iter_commits(f'HEAD...{merge_commit.hexsha}')),
        tuple(repo.iter_commits(f'{merge_commit.hexsha}...{tag.commit.hexsha}'))
    )


def _get_release_note_commits_tuple_for_release(
        current_version: str,
        previous_version: str,
        git_helper: gitutil.GitHelper,
        github_repo: github3.repos.Repository,
) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
    '''
    :return: a tuple of commits which should be included in the release notes
    and a tuple of commits which should not be included in the release notes
    '''
    logger.info('Fetching commits for release notes')

    current_version_tag = git_helper.repo.tag(current_version)
    logger.info(f"Found tag for current version: '{current_version_tag}'")
    previous_version_tag = git_helper.repo.tag(previous_version)
    logger.info(f"Found tag for previous version: '{previous_version_tag}'")

    # if the current version tag and the previous tag are ancestors, just
    # add the range (old method)
    previous_version_tag_commit_sha = git_helper.fetch_head(
        f'refs/tags/{previous_version_tag}'
    )
    current_tag_commit_sha = git_helper.fetch_head(f'refs/tags/{current_version_tag}')
    if git_helper.repo.is_ancestor(previous_version_tag_commit_sha, current_tag_commit_sha):
        logger.info('Previous tag is an ancestor, simple range should be enough.')
        return tuple(git_helper.repo.iter_commits(
            f'{current_tag_commit_sha}...{previous_version_tag_commit_sha}')
        ), tuple()

    # otherwise, use the new method
    # find start of previous release tag
    default_head = git_helper.fetch_head(f'refs/heads/{github_repo.default_branch}')
    if not (previous_branch_starts := git_helper.repo.merge_base(
        default_head,
        previous_version_tag_commit_sha,
    )):
        raise RuntimeError('cannot find the branch start for the previous version')

    previous_branch_start: git.Commit = previous_branch_starts.pop()
    logger.info(
        f'Previous tag not an ancestor. The branch start appears to be {previous_branch_start}'
    )

    # all commits from the branch start to the previous release tag
    # should be removed from the release notes
    filter_out_commits_range = (
        f'{previous_version_tag_commit_sha}...{previous_branch_start}'
    )
    logger.debug(f'{filter_out_commits_range=}')
    filter_out_commits = git_helper.repo.iter_commits(filter_out_commits_range)

    # all commits (and release notes!) not included in {filter_out_commits} should be added to the
    # final generated release notes
    filter_in_commits_range = f'{current_tag_commit_sha}...{previous_branch_start}'
    logger.debug(f'{filter_in_commits_range=}')
    filter_in_commits = git_helper.repo.iter_commits(filter_in_commits_range)

    return tuple(filter_in_commits), tuple(filter_out_commits)


def get_release_note_commits_tuple(
        release_note_version_range: tuple[str | SpecialVersion, str | SpecialVersion],
        git_helper:gitutil.GitHelper,
        github_repo: github3.repos.Repository,
) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
    '''
    :return: a tuple of commits which should be included in the release notes
    and a tuple of commits which should not be included in the release notes
    '''

    from_version, to_version = release_note_version_range

    if from_version is SpecialVersion.INITIAL:
        logger.info('Version appears to be an initial release.')
        if to_version is SpecialVersion.HEAD:
            return tuple(git_helper.repo.iter_commits()), tuple()
        else:
            return tuple(git_helper.repo.iter_commits(git_helper.repo.tag(to_version))), tuple()

    if to_version is SpecialVersion.HEAD:
        logger.info(f"Considering all commits since {from_version}")
        return _list_commits_since_tag(
            repo=git_helper.repo,
            tag=git_helper.repo.tag(from_version),
        )

    logger.info(f'Considering commits in range {from_version}...{to_version}')

    return _get_release_note_commits_tuple_for_release(
        previous_version=from_version,
        current_version=to_version,
        git_helper=git_helper,
        github_repo=github_repo,
    )


def _determine_blocks_to_include(
    filter_in_commits: tuple[git.Commit, ...],
    filter_out_commits: tuple[git.Commit, ...],
    git_helper: gitutil.GitHelper,
    github_helper: github.util.GitHubRepositoryHelper,
) -> set[rnm.SourceBlock]:
    logger.info(
        f'Found {(commit_count := len(filter_in_commits))} relevant commits for release notes '
        f'({len(filter_out_commits)} filtered out).'
    )

    commit_processing_group_size = 200
    processing_group_min_seconds = 200

    if throttled := (commit_count > commit_processing_group_size):
        logger.warning(
            'A large amount of commits needs to be processed for this release. Processing will '
            'be throttled to avoid hitting rate/quota limits.'
        )
        quotient, remainder = divmod(commit_count, commit_processing_group_size)
        estimated_time = (
            quotient * processing_group_min_seconds
            + remainder * (processing_group_min_seconds/commit_processing_group_size)
        )
        logger.warning(
            f'Estimated processing time: {datetime.timedelta(seconds=estimated_time)!s}.'
        )
        if estimated_time > 7200: # 2h, the current timeout for draft-/release steps
            raise RuntimeError(
                'Aborting release-note creation as it will not complete before reaching the '
                'timeout of two hours. Please check whether the number of commits to be scanned '
                'for this release is intentional.'
            )

    # find associated pull requests for commits
    commit_pulls = rnu.request_pull_requests_from_api(
        git_helper=git_helper,
        gh=github_helper.github,
        owner=github_helper.owner,
        repo_name=github_helper.repository_name,
        commits=[*filter_in_commits, *filter_out_commits],
        group_size=commit_processing_group_size,
        min_seconds_per_group=processing_group_min_seconds,
    )
    if throttled:
        logger.info('Finished throttled processing.')
    if commit_pulls:
        logger.info(f'Found {len(commit_pulls)} commits with associated pull requests.')
        for sha, pr_list in commit_pulls.items():
            logger.info(f"\t{sha:.6} -> {','.join(str(pr.number) for pr in pr_list)}")

    source_blocks_to_be_included: set[rnm.SourceBlock] = set()
    for filter_in_commit in filter_in_commits:
        source_blocks_to_be_included.update(rnm.iter_source_blocks(
            source=filter_in_commit,
            content=filter_in_commit.message,
        ))
        for pr in commit_pulls[filter_in_commit.hexsha]:
            if pr.body is None:
                continue
            source_blocks_to_be_included.update(rnm.iter_source_blocks(
                source=pr,
                content=pr.body,
            ))

    logger.info(f'added {len(source_blocks_to_be_included)} source blocks')

    # contains release notes which should be filtered out
    blacklisted_source_blocks: set[rnm.SourceBlock] = set()
    for filter_out_commit in filter_out_commits:
        blacklisted_source_blocks.update(rnm.iter_source_blocks(
            source=filter_out_commit,
            content=filter_out_commit.message,
        ))
        for pr in commit_pulls[filter_out_commit.hexsha]:
            if pr.body is None:
                continue
            blacklisted_source_blocks.update(rnm.iter_source_blocks(
                source=pr,
                content=pr.body,
            ))

    if blacklisted_source_blocks:
        logger.info(f'added {len(blacklisted_source_blocks)} blacklisted source blocks')

        source_blocks_to_be_included -= blacklisted_source_blocks

        logger.info(
            f'Got {len(source_blocks_to_be_included)} source blocks to consider after '
            'removing duplicates.'
        )

    return source_blocks_to_be_included


def fetch_draft_release_notes(
    current_version: str,
    component: gci.componentmodel.Component,
    component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    version_lookup: cnudie.retrieve.VersionLookupByComponent,
    repo_path: str,
):
    known_versions: list[str] = list(version_lookup(component.identity()))

    previous_version = version.greatest_version_before(
        reference_version=current_version,
        versions=known_versions,
        ignore_prerelease_versions=True,
    ) or SpecialVersion.INITIAL

    release_note_version_range = (previous_version, SpecialVersion.HEAD)

    logger.info(
        f'Creating draft-release notes from {previous_version} to current HEAD'
    )

    source = cnudie.util.determine_main_source_for_component(component)
    github_helper = rnu.github_helper_from_github_access(source.access)
    git_helper = rnu.git_helper_from_github_access(source.access, repo_path)

    # make sure _all_ tags are available locally
    git_helper.fetch_tags()

    github_repo: github3.repos.Repository = github_helper.github.repository(
        owner=github_helper.owner,
        repository=github_helper.repository_name,
    )

    # fetch commits for release
    filter_in_commits, filter_out_commits = get_release_note_commits_tuple(
        release_note_version_range=release_note_version_range,
        git_helper=git_helper,
        github_repo=github_repo
    )

    release_note_blocks = _determine_blocks_to_include(
        filter_in_commits=filter_in_commits,
        filter_out_commits=filter_out_commits,
        git_helper=git_helper,
        github_helper=github_helper,
    )

    release_notes: set[rnm.ReleaseNote] = {
        rnm.create_release_notes_obj(
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            source_block=source_block,
            source_component=component,
            current_component=component,
        ) for source_block in release_note_blocks
    }

    return release_notes


def fetch_release_notes(
    component: gci.componentmodel.Component,
    component_descriptor_lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    version_lookup: cnudie.retrieve.VersionLookupByComponent,
    repo_path: str,
    current_version: typing.Optional[str] = None,
    previous_version: typing.Optional[str] = None,
) -> set[rnm.ReleaseNote]:
    ''' Fetches and returns a set of release notes for the specified component.

    :param component: An instance of the Component class from the GCI component model.
    :param repo_path: The (local) path to the git-repository.
    :param current_version: Optional argument to retrieve release notes up to a specific version.
        If not given, the current `HEAD` is used.
    :param previous_version: Optional argument to retrieve release notes starting at a specific \
        version. If not given, the closest version to `current_version` is used.

    :return: A set of ReleaseNote objects for the specified component.
    '''

    # sanity-checks / validation
    if current_version and previous_version:
        current_semver = version.parse_to_semver(current_version)
        previous_semver = version.parse_to_semver(previous_version)

        if current_semver < previous_semver:
            logger.info(
                f'{current_version=} is a predecessor to {previous_version=}. '
                'will not generate release-notes.'
            )
            return set()

        if current_semver == previous_semver:
            logger.info(
                f'Current and previous versions given are equal ({current_version!s}), '
                'will not generate release-notes.'
            )
            return set()

    known_versions: list[str] = list(version_lookup(component.identity()))

    if not previous_version:
        if current_version:
            # if we have a current version, try to find closest match and use it
            previous_version = version.greatest_version_before(
                reference_version=current_version,
                versions=known_versions,
                ignore_prerelease_versions=True,
            )
        else:
            # if no current version was given, use latest version
            previous_version = version.greatest_version(
                versions=known_versions,
                ignore_prerelease_versions=True,
            )
        if not previous_version:
            # if still no previous version could be determined this is probably the first release.
            previous_version = SpecialVersion.INITIAL

    logger.info(
        f'current: {current_version=}, previous: {previous_version=},'
    )

    release_note_version_range = (
        previous_version or SpecialVersion.INITIAL,
        current_version or SpecialVersion.HEAD
    )

    source = cnudie.util.determine_main_source_for_component(component)
    github_helper = rnu.github_helper_from_github_access(source.access)
    git_helper = rnu.git_helper_from_github_access(source.access, repo_path)

    # make sure _all_ tags are available locally
    git_helper.fetch_tags()

    github_repo: github3.repos.Repository = github_helper.github.repository(
        owner=github_helper.owner,
        repository=github_helper.repository_name,
    )

    # fetch commits for release
    filter_in_commits, filter_out_commits = get_release_note_commits_tuple(
        release_note_version_range=release_note_version_range,
        git_helper=git_helper,
        github_repo=github_repo
    )

    release_note_blocks = _determine_blocks_to_include(
        filter_in_commits=filter_in_commits,
        filter_out_commits=filter_out_commits,
        git_helper=git_helper,
        github_helper=github_helper,
    )

    release_notes: set[rnm.ReleaseNote] = {
        rnm.create_release_notes_obj(
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            source_block=source_block,
            source_component=component,
            current_component=component,
        ) for source_block in release_note_blocks
    }

    return release_notes
