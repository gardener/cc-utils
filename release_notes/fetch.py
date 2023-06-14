import logging
import typing

import gci.componentmodel
import git
import github3.repos
import semver

import cnudie.retrieve
import cnudie.util
import github.release_notes.util as ghrnu
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
) -> tuple[git.Commit]:
    '''Return a list of between the given tag and HEAD

    :return: a tuple of commits'''
    if repo.is_ancestor(tag.commit, 'HEAD'):
        return tuple(repo.iter_commits(f'HEAD...{tag.commit.hexsha}'))

    if (
        not (merge_commit_list := repo.merge_base('HEAD', tag))
        or not (merge_commit := merge_commit_list.pop())
    ):
        raise RuntimeError('cannot find merge base')
    return tuple(repo.iter_commits(f'HEAD...{merge_commit.hexsha}'))


def _get_release_note_commits_tuple_for_minor_release(
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
    logger.info('creating new minor release')
    # previous minor version with patch, prerelease, build, ... set to 0 / None
    previous_minor_version = semver.VersionInfo(major=previous_version.major,
                                                minor=previous_version.minor)
    previous_minor_version_tag = git_helper.repo.tag(component_versions[previous_minor_version])
    if not previous_minor_version_tag:
        raise RuntimeError('cannot find previous minor version in component versions / tags')
    logger.info(f'found previous minor tag: {previous_minor_version_tag}')

    # if the current version tag and the previous minor tag are ancestors, just
    # add the range (old method)
    previous_version_tag_commit_sha = git_helper.fetch_head(
        f'refs/tags/{previous_minor_version_tag}'
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
        previous_version: semver.VersionInfo,
        previous_version_tag: git.TagReference,
        component_versions: dict[semver.VersionInfo, str],
        git_helper,
        current_version_tag: git.TagReference,
        current_version: semver.VersionInfo,
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
        ), tuple()

    # new major release (not supported yet)
    if current_version.major != previous_version.major:
        raise NotImplementedError(
            'generating release notes for new major releases is not supported yet.'
        )

    # new minor release
    if current_version.minor != previous_version.minor:
        return _get_release_note_commits_tuple_for_minor_release(
            previous_version=previous_version,
            component_versions=component_versions,
            git_helper=git_helper,
            github_repo=github_repo,
            current_version_tag=current_version_tag
        )

    # new patch release
    logger.info(f'creating new patch release from {current_version_tag} to {previous_version_tag}')
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
        repo_path: str,
        current_version: typing.Optional[semver.VersionInfo] = None,
        previous_version: typing.Optional[semver.VersionInfo] = None,
) -> set[rnm.ReleaseNote]:
    ''' Fetches and returns a set of release notes for the specified component.

    :param component: An instance of the Component class from the GCI component model.
    :param repo_path: The (local) path to the git-repository.
    :param current_version: Optional argument to retrieve release notes up to a specific version.
        If not given, the current `HEAD` is used.
    :param previous_version: Optional argument to retrieve release notes starting at a specific
        version. If not given, the closest version to `current_version` is used

    :return: A set of ReleaseNote objects for the specified component.
    '''
    source = cnudie.util.determine_main_source_for_component(component)
    github_helper = ghrnu.github_helper_from_github_access(source.access)
    git_helper = ghrnu.git_helper_from_github_access(source.access, repo_path)
    # make sure _all_ tags are available locally
    git_helper.fetch_tags()

    # find all available versions
    component_versions: dict[semver.VersionInfo, str] = {}
    for ver in cnudie.retrieve.component_versions(
            component.name,
            component.current_repository_ctx()
    ):
        parsed_version = version.parse_to_semver(ver)
        if parsed_version.prerelease:  # ignore pre-releases
            continue
        component_versions[parsed_version] = ver

    if not current_version:
        current_version_tag = None
    else:
        current_version_tag = git_helper.repo.tag(component_versions[current_version])
        if not current_version_tag:
            raise RuntimeError(f'cannot find ref {source.access.ref} in repo')

    if not previous_version:
        previous_version = rnu.find_next_smallest_version(
            list(component_versions.keys()), current_version
        )
    previous_version_tag: typing.Optional[git.TagReference] = None
    if previous_version:
        previous_version_tag = git_helper.repo.tag(component_versions[previous_version])

    logger.debug(f'current: {current_version=}, {current_version_tag=}, ' +
                 f'previous: {previous_version=}, {previous_version_tag=}')

    github_repo: github3.repos.Repository = github_helper.github.repository(
        github_helper.owner,
        github_helper.repository_name
    )

    # fetch commits for release
    filter_in_commits, filter_out_commits = get_release_note_commits_tuple(
        previous_version=previous_version,
        previous_version_tag=previous_version_tag,
        component_versions=component_versions,
        git_helper=git_helper,
        current_version_tag=current_version_tag,
        current_version=current_version,
        github_repo=github_repo
    )

    logger.info(f'requesting associated pull requests for {len(filter_in_commits)} ' +
                f'filter in and {len(filter_out_commits)} filter out commits')

    # find associated pull requests for commits
    commit_pulls = rnu.request_pull_requests_from_api(
        git_helper=git_helper,
        gh=github_helper.github,
        owner=github_repo.owner,
        repo_name=github_repo.name,
        commits=[*filter_in_commits, *filter_out_commits]
    )
    logger.info(f'commit_pulls: {len(commit_pulls)}')

    # contains release notes which should be filtered out
    blacklisted_source_blocks: set[rnm.SourceBlock] = set()
    for filter_out_commit in filter_out_commits:
        blacklisted_source_blocks.update(rnm.iter_source_blocks(filter_out_commit.message))
        for pr in commit_pulls[filter_out_commit.hexsha]:
            blacklisted_source_blocks.update(rnm.iter_source_blocks(pr.body))
    logger.info(f'added {len(blacklisted_source_blocks)} blacklisted source blocks')

    release_notes: set[rnm.ReleaseNote] = set()
    for filter_in_commit in filter_in_commits:
        # by associated pull requests
        for pr in commit_pulls[filter_in_commit.hexsha]:
            if pr.body is None:
                continue
            release_notes.update(
                rnm.create_release_note_obj(
                    source_block=z,
                    source_commit=filter_in_commit,
                    raw_body=pr.body,
                    author=rnm.author_from_pull_request(pr),
                    target=pr,
                    source_component=component,
                    current_component=component,
                ) for z in rnm.iter_source_blocks(pr.body)
            )
        # by commit
        release_notes.update(rnm.create_release_note_obj(
            source_block=z,
            source_commit=filter_in_commit,
            raw_body=filter_in_commit.message,
            author=rnm.author_from_commit(filter_in_commit),
            target=filter_in_commit,
            source_component=component,
            current_component=component,
        ) for z in rnm.iter_source_blocks(filter_in_commit.message))
    return release_notes
