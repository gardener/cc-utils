import logging
import typing
import datetime

import gci.componentmodel
import git
import github3.repos
import semver

import cnudie.retrieve
import cnudie.util
import gitutil
import release_notes.model as rnm
import release_notes.utils as rnu
import version

logger = logging.getLogger(__name__)


def _list_commits_between_tags(
        repo: git.Repo,
        main_tag: git.TagReference,
        other_tag: git.TagReference
) -> tuple[git.Commit]:
    ''' If the tags are linear to each other (main_tag ancestor of other_tag or
    vice versa), all commits between the tags are returned. Otherwise, all
    commits between the merge base (first common ancestor) and the main_branch
    are returned.

    :return: a tuple of commits between the two tags '''
    if repo.is_ancestor(main_tag.commit, other_tag.commit) or \
            repo.is_ancestor(other_tag.commit, main_tag.commit):
        return tuple(repo.iter_commits(f'{main_tag.commit.hexsha}...{other_tag.commit.hexsha}'))

    if not (merge_commit_list := repo.merge_base(main_tag, other_tag)) or not \
            (merge_commit := merge_commit_list.pop()):
        raise RuntimeError('cannot find merge base')
    return tuple(repo.iter_commits(f'{main_tag.commit.hexsha}...{merge_commit.hexsha}'))


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
        previous_version: semver.VersionInfo,
        component_versions: dict[semver.VersionInfo, str],
        git_helper: gitutil.GitHelper,
        github_repo: github3.repos.Repository,
        current_version_tag: git.TagReference,
) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
    '''
    :return: a tuple of commits which should be included in the release notes
    and a tuple of commits which should not be included in the release notes
    '''
    logger.info('creating new release')

    previous_version_tag = git_helper.repo.tag(component_versions[previous_version])
    if not previous_version_tag:
        raise RuntimeError(
            f"cannot find previous version '{previous_version!s}' in component versions / tags."
        )
    logger.info(f'found previous minor tag: {previous_version_tag}')

    # if the current version tag and the previous minor tag are ancestors, just
    # add the range (old method)
    previous_version_tag_commit_sha = git_helper.fetch_head(
        f'refs/tags/{previous_version_tag}'
    )
    current_tag_commit_sha = git_helper.fetch_head(f'refs/tags/{current_version_tag}')
    if git_helper.repo.is_ancestor(previous_version_tag_commit_sha, current_tag_commit_sha):
        logger.info('it\'s an ancestor. simple range should be enough.')
        return tuple(git_helper.repo.iter_commits(
            f'{current_tag_commit_sha}...{previous_version_tag_commit_sha}')
        ), tuple()

    # otherwise, use the new method
    # find start of previous minor-release tag
    default_head = git_helper.fetch_head(f'refs/heads/{github_repo.default_branch}')
    if not (previous_branch_starts := git_helper.repo.merge_base(
        default_head,
        previous_version_tag_commit_sha,
    )):
        raise RuntimeError('cannot find the branch start for the previous version')

    previous_branch_start: git.Commit = previous_branch_starts.pop()
    logger.info(f'it\'s not an ancestor. the branch start appears to be {previous_branch_start}')

    # all commits from the branch start to the previous minor-release tag
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
        previous_version: semver.VersionInfo | None,
        previous_version_tag: git.TagReference,
        component_versions: dict[semver.VersionInfo, str],
        git_helper,
        current_version_tag: git.TagReference,
        current_version: semver.VersionInfo | None,
        github_repo: github3.repos.Repository,
) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
    '''
    :return: a tuple of commits which should be included in the release notes
    and a tuple of commits which should not be included in the release notes
    '''
    # initial release
    if not previous_version or len(component_versions) == 1:
        logger.info('version appears to be an initial release.')
        # just return all commits starting from the current_version_tag
        return tuple(git_helper.repo.iter_commits(current_version_tag)), tuple()

    if not current_version:
        logger.info('No current version specified. Start fetching of release notes at HEAD')
        return _list_commits_since_tag(
            repo=git_helper.repo,
            tag=previous_version_tag,
        )
    # new major release
    if current_version.major != previous_version.major:
        logger.info(
            f"creating new major release '{current_version!s}'"
        )
        previous_minor_version = semver.VersionInfo(
            major=previous_version.major,
            minor=previous_version.minor
        )
        return _get_release_note_commits_tuple_for_release(
            previous_version=previous_minor_version,
            component_versions=component_versions,
            git_helper=git_helper,
            github_repo=github_repo,
            current_version_tag=current_version_tag
        )

    # new minor release
    if current_version.minor != previous_version.minor and current_version.patch == 0:
        logger.info(
            f"creating new minor release '{current_version!s}'"
        )
        previous_minor_version = semver.VersionInfo(
            major=previous_version.major,
            minor=previous_version.minor
        )
        return _get_release_note_commits_tuple_for_release(
            previous_version=previous_minor_version,
            component_versions=component_versions,
            git_helper=git_helper,
            github_repo=github_repo,
            current_version_tag=current_version_tag
        )

    # new patch release
    logger.info(f'creating new patch release from {previous_version_tag} to {current_version_tag}')
    if previous_version_tag is None:
        raise RuntimeError(
            'cannot create patch-release notes because previous version cannot be found'
        )
    return _list_commits_between_tags(
            git_helper.repo,
            current_version_tag,
            previous_version_tag
    ), tuple()


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

    if current_version and previous_version:
        if version.parse_to_semver(current_version) < version.parse_to_semver(previous_version):
            logger.info(
                f'{current_version=} is a predecessor to {previous_version=}. '
                'Will not generate release-notes.'
            )
            return set()

    source = cnudie.util.determine_main_source_for_component(component)
    github_helper = rnu.github_helper_from_github_access(source.access)
    git_helper = rnu.git_helper_from_github_access(source.access, repo_path)

    # make sure _all_ tags are available locally
    git_helper.fetch_tags()

    # find all available versions
    component_versions: dict[semver.VersionInfo, str] = {}

    for ver in version_lookup(component.identity()):
        parsed_version = version.parse_to_semver(ver)
        if parsed_version.prerelease:  # ignore pre-releases
            continue
        component_versions[parsed_version] = ver

    if current_version:
        current_version_tag = git_helper.repo.tag(current_version)
        if not current_version_tag:
            raise RuntimeError(f'cannot find ref {source.access.ref} in repo')
    else:
        current_version_tag = None

    if not previous_version:
        # no previous version will exist on first release
        previous_version_tag: typing.Optional[git.TagReference] = None
        previous_version = rnu.find_next_smallest_version(
            available_versions=list(component_versions.keys()),
            current_version=version.parse_to_semver(current_version) if current_version else None,
        )
        if previous_version:
            previous_version = str(previous_version)

    if previous_version:
        # we need to use the original previous version as found in the ocm-repo to refer to
        # the tag or we risk losing the leading 'v' if it is present.
        # TODO: This may happen after retrieving the previous version the component_versions' keys.
        # This function should be split up and refactored to avoid these headaches. Ideally use
        # str where possible, as they are the closest representation of our release-versions.
        original_version = component_versions[version.parse_to_semver(previous_version)]
        previous_version_tag = git_helper.repo.tag(original_version)

    logger.info(
        f'current: {current_version=}, {current_version_tag=}, '
        f'previous: {previous_version=}, {previous_version_tag=}'
    )

    github_repo: github3.repos.Repository = github_helper.github.repository(
        github_helper.owner,
        github_helper.repository_name
    )

    # fetch commits for release
    filter_in_commits, filter_out_commits = get_release_note_commits_tuple(
        previous_version=version.parse_to_semver(previous_version) if previous_version else None,
        previous_version_tag=previous_version_tag,
        component_versions=component_versions,
        git_helper=git_helper,
        current_version_tag=current_version_tag,
        current_version=version.parse_to_semver(current_version) if current_version else None,
        github_repo=github_repo
    )

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
        owner=github_repo.owner,
        repo_name=github_repo.name,
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

    release_notes: set[rnm.ReleaseNote] = {
        rnm.create_release_notes_obj(
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            source_block=source_block,
            source_component=component,
            current_component=component,
        ) for source_block in source_blocks_to_be_included
    }

    return release_notes
