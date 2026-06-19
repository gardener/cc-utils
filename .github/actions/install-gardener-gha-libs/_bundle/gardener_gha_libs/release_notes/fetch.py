import datetime
import enum
import logging
import os

import git
import github3.repos

import cnudie.retrieve
import gitutil
import ocm
import ocm.util
import release_notes.model as rnm
import release_notes.ocm as rno
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
    '''
    Return a list of between the given tag and HEAD

    :return: a tuple of commits
    '''
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
    current_version_ref_commit: git.Commit | None,
    previous_version: str,
    git_helper: gitutil.GitHelper,
    github_repo: github3.repos.Repository,
) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
    '''
    current_version: version which defines upper boundary of versions to honour (needed if fetching
                     release-notes for a non-head release-branch)
    current_version_ref_commit: if tag is not present in remote (e.g. if pipeline does not
                                want to to publish it, yet), pass-in commit to use for
                                walking up to previous release-tag/commit.
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
    if not current_version_ref_commit:
        current_version_ref_commit = git_helper.fetch_head(f'refs/tags/{current_version_tag}')

    if git_helper.repo.is_ancestor(previous_version_tag_commit_sha, current_version_ref_commit):
        logger.info('Previous tag is an ancestor, simple range should be enough.')
        return tuple(git_helper.repo.iter_commits(
            f'{current_version_ref_commit}...{previous_version_tag_commit_sha}')
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
    filter_in_commits_range = f'{current_version_ref_commit}...{previous_branch_start}'
    logger.debug(f'{filter_in_commits_range=}')
    filter_in_commits = git_helper.repo.iter_commits(filter_in_commits_range)

    return tuple(filter_in_commits), tuple(filter_out_commits)


def get_release_note_commits_tuple(
    release_note_version_range: tuple[str | SpecialVersion, str | SpecialVersion],
    git_helper:gitutil.GitHelper,
    github_repo: github3.repos.Repository,
    current_version_ref_commit: git.Commit | None=None,
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
        current_version_ref_commit=current_version_ref_commit,
        git_helper=git_helper,
        github_repo=github_repo,
    )


def _determine_blocks_to_include(
    filter_in_commits: tuple[git.Commit, ...],
    filter_out_commits: tuple[git.Commit, ...],
    github_access: ocm.GithubAccess,
    git_helper: gitutil.GitHelper,
    github_api_lookup: rnu.GithubApiLookup,
    component: ocm.Component,
) -> set[rnm.SourceBlock]:
    logger.info(
        f'Found {(commit_count := len(filter_in_commits))} relevant commits for release notes '
        f'({len(filter_out_commits)} filtered out).'
    )
    logger.info('the following commit-digests will be checked:')
    for commit in filter_in_commits:
        logger.info(f'{commit.hexsha}')

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
        github_api_lookup=github_api_lookup,
        github_access=github_access,
        commits=[*filter_in_commits, *filter_out_commits],
        group_size=commit_processing_group_size,
        min_seconds_per_group=processing_group_min_seconds,
        component=component,
    )
    if throttled:
        logger.info('Finished throttled processing.')
    if commit_pulls:
        logger.info(f'Found {len(commit_pulls)} commits with associated pull requests.')
        for sha, pr_list in commit_pulls.items():
            logger.info(f"\t{sha:.6} -> {','.join(str(pr.number) for pr in pr_list)}")
    else:
        logger.info('Did not find any associated pullrequests')

    source_blocks_to_be_included: set[rnm.SourceBlock] = set()
    for filter_in_commit in filter_in_commits:
        blocks, _ = rnm.iter_source_blocks(
            source=filter_in_commit,
            content=filter_in_commit.message,
        )
        source_blocks_to_be_included.update(blocks)
        for pr in commit_pulls[filter_in_commit.hexsha]:
            if pr.body is None:
                continue
            blocks, _ = rnm.iter_source_blocks(
                source=pr,
                content=pr.body,
            )
            source_blocks_to_be_included.update(blocks)

    logger.info(f'added {len(source_blocks_to_be_included)} source blocks')

    # contains release notes which should be filtered out
    blacklisted_source_blocks: set[rnm.SourceBlock] = set()
    for filter_out_commit in filter_out_commits:
        blocks, _ = rnm.iter_source_blocks(
            source=filter_out_commit,
            content=filter_out_commit.message,
        )
        blacklisted_source_blocks.update(blocks)
        for pr in commit_pulls[filter_out_commit.hexsha]:
            if pr.body is None:
                continue
            blocks, _ = rnm.iter_source_blocks(
                source=pr,
                content=pr.body,
            )
            blacklisted_source_blocks.update(blocks)

    if blacklisted_source_blocks:
        logger.info(f'added {len(blacklisted_source_blocks)} blacklisted source blocks')

        source_blocks_to_be_included -= blacklisted_source_blocks

        logger.info(
            f'Got {len(source_blocks_to_be_included)} source blocks to consider after '
            'removing duplicates.'
        )

    return source_blocks_to_be_included


def fetch_release_notes(
    component: ocm.Component,
    version_lookup: cnudie.retrieve.VersionLookupByComponent,
    git_helper: gitutil.GitHelper,
    github_api_lookup: rnu.GithubApiLookup,
    version_whither: str | None=None,
    version_whence: str | None=None,
    version_whither_ref_commit: git.Commit | None=None,
    is_draft: bool=False,
) -> rnm.ReleaseNotesDoc | None:
    '''
    Fetches and returns a set of release notes for the specified component.

    :param component: the OCM Component for which to retrieve release-notes.
    :param version_wither: Optional argument to retrieve release notes up to specified version.
        If not given, the current `HEAD` is used.
    :param version_whence: Optional argument to retrieve release notes starting at a specific \
        version. If not given, the closest version to `version_whither` is used.

    :return: A set of ReleaseNotesDoc objects for the specified component.
    '''

    # sanity-checks / validation
    if version_whither and version_whence:
        current_semver = version.parse_to_semver(version_whither)
        previous_semver = version.parse_to_semver(version_whence)

        if current_semver < previous_semver:
            logger.info(
                f'{version_whither=} is a predecessor to {version_whence=}. '
                'will not generate release-notes.'
            )
            return None

        if current_semver == previous_semver:
            logger.info(
                f'Current and previous versions given are equal ({version_whither!s}), '
                'will not generate release-notes.'
            )
            return None

    known_versions: list[str] = [
        v for v in
        version_lookup(component.identity())
        if version.is_final(v)
    ]

    if not version_whence:
        if version_whither:
            # if we have a current version, try to find closest match and use it
            version_whence = version.find_predecessor(
                version=version_whither,
                versions=known_versions,
            )
        else:
            # if no current version was given, use greatest version
            version_whence = version.greatest_version(
                versions=known_versions,
                ignore_prerelease_versions=True,
            )
        if not version_whence:
            # if still no previous version could be determined this is probably the first release.
            version_whence = SpecialVersion.INITIAL

    if not version_whither or is_draft:
        version_whither = SpecialVersion.HEAD

    logger.info(f'current: {version_whither=}, {version_whence=}')

    release_note_version_range = (version_whence, version_whither)

    source = ocm.util.main_source(component)
    # todo: check access-type / handle unsupported types (non-github)
    github_access: ocm.GithubAccess = source.access
    hostname = github_access.hostname()
    org_name = github_access.org_name()
    repo_name = github_access.repository_name()

    github_api = github_api_lookup(github_access.repoUrl)

    # make sure _all_ tags are available locally
    git_helper.fetch_tags()

    github_repo: github3.repos.Repository = github_api.repository(
        owner=github_access.org_name(),
        repository=github_access.repository_name(),
    )

    # fetch commits for release
    filter_in_commits, filter_out_commits = get_release_note_commits_tuple(
        release_note_version_range=release_note_version_range,
        current_version_ref_commit=version_whither_ref_commit,
        git_helper=git_helper,
        github_repo=github_repo,
    )

    release_note_blocks = _determine_blocks_to_include(
        filter_in_commits=filter_in_commits,
        filter_out_commits=filter_out_commits,
        github_access=github_access,
        git_helper=git_helper,
        github_api_lookup=github_api_lookup,
        component=component,
    )

    release_notes = [
        release_note_block.as_release_note_entry(
            hostname=hostname,
            org=org_name,
            repo=repo_name,
        ) for release_note_block in release_note_blocks
    ]

    if not release_notes:
        return None

    return rnm.ReleaseNotesDoc(
        ocm=rnm.ReleaseNotesOcmRef(
            component_name=component.name,
            component_version=component.version,
        ),
        release_notes=release_notes,
    )


def collect_release_notes(
    git_helper: gitutil.GitHelper,
    release_version: str,
    component: ocm.Component,
    version_lookup: ocm.VersionLookup,
    github_api_lookup: rnu.GithubApiLookup,
    version_whither_ref_commit: git.Commit | None=None,
    is_draft: bool=False,
) -> tuple[rnm.ReleaseNotesDoc | None, list[rnm.ReleaseNotesDoc]]:
    repo_path = git_helper.repo_path
    local_release_notes_path = os.path.join(repo_path, '.ocm/release-notes')

    release_notes_docs = list(rno.read_release_notes_from_dir(
        release_notes_docs_dir=local_release_notes_path,
        reference_version=release_version,
    ))
    for release_notes_doc in release_notes_docs:
        if (
            not release_notes_doc.ocm
            or not release_notes_doc.ocm.component_name
            or not release_notes_doc.ocm.component_version
        ):
            release_notes_doc.ocm = rnm.ReleaseNotesOcmRef(
                component_name=component.name,
                component_version=component.version,
            )

    if fetched_release_notes_doc := fetch_release_notes(
        component=component,
        version_lookup=version_lookup,
        git_helper=git_helper,
        github_api_lookup=github_api_lookup,
        version_whither=release_version,
        version_whither_ref_commit=version_whither_ref_commit,
        is_draft=is_draft,
    ):
        release_notes_docs.append(fetched_release_notes_doc)

    grouped_release_notes_docs = rno.group_release_notes_docs(release_notes_docs)

    component_id = component.identity()

    matching_component_release_notes_docs = [
        release_notes_doc
        for release_notes_doc in grouped_release_notes_docs
        if release_notes_doc.component_id == component_id
    ]
    if len(matching_component_release_notes_docs) > 1:
        raise RuntimeError(f'Multiple ReleaseNotesDocs found for {component_id=} - this is a bug')

    if matching_component_release_notes_docs:
        component_release_notes_doc = matching_component_release_notes_docs[0]
    else:
        component_release_notes_doc = None

    subcomponent_release_notes_docs = [
        release_notes_doc
        for release_notes_doc in grouped_release_notes_docs
        if release_notes_doc.component_id != component_id
    ]

    return component_release_notes_doc, subcomponent_release_notes_docs
