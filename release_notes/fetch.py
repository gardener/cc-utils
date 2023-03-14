import logging
import typing

import gci.componentmodel
import git
import github3.repos
import semver

import cnudie.retrieve
import cnudie.util
import version
from github.release_notes.util import git_helper_from_github_access, github_helper_from_github_access
from release_notes import association, models

logger = logging.getLogger(__name__)


def _find_previous_version(available_versions: list[semver.VersionInfo],
                           current_version: semver.VersionInfo) -> typing.Optional[semver.VersionInfo]:
    # find version before the requested version and sort by semver
    return max((z for z in sorted(available_versions) if z < current_version), default=None)


def _iter_tags(repo: git.Repo, newest_tag: git.TagReference, oldest_tag: git.TagReference) -> tuple[git.Commit]:
    """ Iterates between two tags, even if they aren't linear.
    """
    # even though it's a list, it contains max. 1 commit since the --all flag is not passed.
    if not (merge_commit_list := repo.merge_base(newest_tag, oldest_tag)):
        raise RuntimeError("cannot find merge base")
    merge_commit: git.Commit = merge_commit_list.pop()

    # if the previous tag is after the merge-base, return all commits between merge-base and current tag
    if merge_commit.authored_date < oldest_tag.commit.authored_date:
        return tuple(repo.iter_commits(f"{newest_tag.commit.hexsha}...{merge_commit.hexsha}"))

    # otherwise the tags should be linear
    return tuple(repo.iter_commits(f"{newest_tag.commit.hexsha}...{oldest_tag.commit.hexsha}"))


class ReleaseNotesComponentAccess:
    def __init__(self,
                 component: gci.componentmodel.Component,
                 repo_path: str,
                 current_version: typing.Optional[semver.VersionInfo] = None):
        self.component = component
        self.source = cnudie.util.determine_main_source_for_component(self.component)

        self.github_helper = github_helper_from_github_access(self.source.access)
        self.git_helper = git_helper_from_github_access(self.source.access, repo_path)

        # find all available versions
        self.component_versions: dict[semver.VersionInfo, str] = {}
        for ver in cnudie.retrieve.component_versions(self.component.name, self.component.current_repository_ctx()):
            parsed_version = version.parse_to_semver(ver)
            if parsed_version.prerelease:  # ignore pre-releases
                continue
            self.component_versions[parsed_version] = ver

        # TODO: remove this. just for debugging.
        # fakes (tag-) versions to the component descriptor
        for ver in ["v1.66.0", "v1.66.1", "v1.67.0", "v1.67.1", "v1.67.2"]:
            self.component_versions[version.parse_to_semver(ver)] = ver

        if current_version is None:
            if self.source.version is None:
                raise ValueError(f"current_version not passed and not found in component source")
            current_version = version.parse_to_semver(self.source.version)

            # access tag from component
            self.current_version_tag = self.git_helper.repo.tag(self.source.access.ref)
        else:
            # access tag from current version
            self.current_version_tag = self.git_helper.repo.tag(self.component_versions[current_version])
        self.current_version = current_version

        # find tag in repository
        if not self.current_version_tag:
            raise RuntimeError(f"cannot find ref {self.source.access.ref} in repo")

        # find previous version
        self.previous_version = _find_previous_version(list(self.component_versions.keys()), current_version)
        if self.previous_version is not None:
            self.previous_version_tag = self.git_helper.repo.tag(self.component_versions[self.previous_version])

        logger.debug(f"current: {self.current_version=}, {self.current_version_tag=}, " +
                     f"previous: {self.previous_version=}, {self.previous_version_tag=}")

        self.github_repo: github3.repos.Repository = self.github_helper.github.repository(
            self.github_helper.owner,
            self.github_helper.repository_name
        )

    def _lists_for_new_initial_release(self) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
        """ returns commits for new initial-release (all commits in repository starting from the tag)
        """
        # just return all commits starting from the current_version_tag
        return tuple(self.git_helper.repo.iter_commits(self.current_version_tag)), tuple()

    def _list_for_new_patch_release(self) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
        """ return commits for new patch-release
        """
        logger.info(f"creating new patch release from {self.current_version_tag} to {self.previous_version_tag}")
        if self.previous_version_tag is None:
            raise RuntimeError("cannot create patch-release notes because previous version cannot be found")
        return _iter_tags(self.git_helper.repo, self.current_version_tag, self.previous_version_tag), tuple()

    def _list_for_new_minor_release(self) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
        """ return commits for new minor-release
        """
        logger.info("creating new minor release")
        # previous minor version with patch, prerelease, build, ... set to 0 / None
        previous_minor_version = semver.VersionInfo(major=self.previous_version.major,
                                                    minor=self.previous_version.minor)
        previous_minor_version_tag = self.git_helper.repo.tag(self.component_versions[previous_minor_version])
        if not previous_minor_version_tag:
            raise RuntimeError("cannot find previous minor version in component versions / tags")
        logger.info(f"found previous minor tag: {previous_minor_version_tag}")

        # if the current version tag and the previous minor tag are ancestors, just add the range (old method)
        if self.git_helper.repo.is_ancestor(previous_minor_version_tag.commit, self.current_version_tag.commit):
            logger.info("it's an ancestor. simple range should be enough.")
            return _iter_tags(self.git_helper.repo, self.current_version_tag, previous_minor_version_tag), tuple()

        # otherwise, use the new method
        # find start of previous minor-release tag
        if not (previous_branch_starts := self.git_helper.repo.merge_base(self.github_repo.default_branch,
                                                                          previous_minor_version_tag.commit.hexsha)):
            raise RuntimeError("cannot find the branch start for the previous version")
        previous_branch_start: git.Commit = previous_branch_starts.pop()
        logger.info(f"it's not an ancestor. the branch start appears to be {previous_branch_start}")

        # all commits from the branch start to the previous minor-release tag should be removed from the release notes
        filter_out_commits_range = f"{previous_minor_version_tag.commit.hexsha}...{previous_branch_start}"
        logger.debug(f"{filter_out_commits_range=}")
        filter_out_commits = self.git_helper.repo.iter_commits(filter_out_commits_range)

        # all commits (and release notes!) not included in {filter_out_commits} should be added to the
        # final generated release notes
        filter_in_commits_range = f"{self.current_version_tag.commit.hexsha}...{previous_branch_start}"
        logger.debug(f"{filter_in_commits_range=}")
        filter_in_commits = self.git_helper.repo.iter_commits(filter_in_commits_range)

        return tuple(filter_in_commits), tuple(filter_out_commits)

    def list_commits_for_current_version(self) -> tuple[tuple[git.Commit], tuple[git.Commit]]:
        # initial release?
        if self.previous_version is None or len(self.component_versions) == 1:
            logger.info("version appears to be an initial release.")
            return self._lists_for_new_initial_release()

        # new major release
        if self.current_version.major != self.previous_version.major:
            raise NotImplementedError("generating release notes for new major releases is not supported yet.")

        # new minor release
        if self.current_version.minor != self.previous_version.minor:
            return self._list_for_new_minor_release()

        # new patch release
        return self._list_for_new_patch_release()


def create_note_blocks(release_notes: set[models.ReleaseNote]) -> str:
    return '\n\n'.join(z.to_block_str() for z in release_notes)


def fetch_release_notes(
        component: gci.componentmodel.Component,
        repo_path: str,
        for_version: typing.Optional[semver.VersionInfo] = None,
):
    ctl = ReleaseNotesComponentAccess(
        component=component,
        repo_path=repo_path,
        current_version=for_version,
    )
    # fetch commits for release
    filter_in_commits, filter_out_commits = ctl.list_commits_for_current_version()

    logger.info(f"requesting associated pulls for {len(filter_in_commits)} " +
                f"filter in and {len(filter_out_commits)} filter out commits")

    # find associated pull requests for commits
    commit_pulls = association.request_pulls_from_api(
        repo=ctl.git_helper.repo,
        gh=ctl.github_helper.github,
        owner=ctl.github_repo.owner,
        repo_name=ctl.github_repo.name,
        commits=[*filter_in_commits, *filter_out_commits]
    )
    logger.info(f"commit_pulls: {len(commit_pulls)}")

    # contains release notes which should be filtered out
    blacklisted_source_blocks: set[models.SourceBlock] = set()
    for filter_out_commit in filter_out_commits:
        blacklisted_source_blocks.update(models.list_source_blocks(filter_out_commit.message))
        for pr in commit_pulls[filter_out_commit.hexsha]:
            blacklisted_source_blocks.update(models.list_source_blocks(pr.body))
    logger.info(f"added {len(blacklisted_source_blocks)} blacklisted source blocks")

    release_notes: set[models.ReleaseNote] = set()
    for filter_in_commit in filter_in_commits:
        # by associated pull requests
        for pr in commit_pulls[filter_in_commit.hexsha]:
            release_notes.update(models.create_release_note_obj(
                block=z,
                source_commit=filter_in_commit,
                raw_body=pr.body,
                author=models.author_from_pull_request(pr),
                targets=pr,
                source_component=component,
                current_component=component,
            ) for z in models.list_source_blocks(pr.body))
        # by commit
        release_notes.update(models.create_release_note_obj(
            block=z,
            source_commit=filter_in_commit,
            raw_body=filter_in_commit.message,
            author=models.author_from_commit(filter_in_commit),
            targets=filter_in_commit,
            source_component=component,
            current_component=component,
        ) for z in models.list_source_blocks(filter_in_commit.message))
    return release_notes
