# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import deprecated
import enum
import io
import re
import sys

from typing import Iterable, Tuple
from pydash import _

import requests

import github3
from github3.github import GitHub
from github3.repos.release import Release
from github3.exceptions import NotFoundError, ForbiddenError
from github3.orgs import Team

import ccc.github
import ci.util
import product.model
import version

from product.model import DependencyBase
from model.github import GithubConfig


class RepoPermission(enum.Enum):
    PULL = "pull"
    PUSH = "push"
    ADMIN = "admin"


class GitHubRepoBranch(object):
    '''Instances of this class represent a specific branch of a given GitHub repository.
    '''
    def __init__(
        self,
        github_config: GithubConfig,
        repo_owner: str,
        repo_name: str,
        branch: str,
    ):
        self._github_config = ci.util.not_none(github_config)
        self._repo_owner = ci.util.not_empty(repo_owner)
        self._repo_name = ci.util.not_empty(repo_name)
        self._branch = ci.util.not_empty(branch)

    def github_repo_path(self):
        return f'{self._repo_owner}/{self._repo_name}'

    def github_config(self):
        return self._github_config

    def repo_owner(self):
        return self._repo_owner

    def repo_name(self):
        return self._repo_name

    def branch(self):
        return self._branch


class RepositoryHelperBase(object):
    GITHUB_TIMESTAMP_UTC_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

    def __init__(
        self,
        owner: str,
        name: str,
        default_branch: str='master',
        github_cfg: GithubConfig=None,
        github_api: GitHub=None,
    ):
        '''
        Args:
            owner (str):    repository owner (also called organisation in GitHub)
            name (str):     repository name
            default_branch (str): branch to use for operations when not specified
            github_cfg (GithubConfig): cfg to construct github api object from
            github_api (GitHub): github api to use

        Exactly one of `github_cfg` and `github_api` must be passed as argument.
        Passing a GitHub object is more flexible (but less convenient).
        '''
        if not (bool(github_cfg) ^ bool(github_api)):
            raise ValueError('exactly one of github_api and github_cfg must be given')

        if github_cfg:
            self.github = ccc.github.github_api(github_cfg)
        else:
            self.github = github_api

        self.repository = self._create_repository(
            owner=owner,
            name=name
        )
        self.owner = owner
        self.repository_name = name

        self.default_branch = default_branch

    def _create_repository(self, owner: str, name: str):
        try:
            repository = self.github.repository(
                    owner=owner,
                    repository=name
            )
            return repository
        except NotFoundError as nfe:
            raise RuntimeError(
                'failed to retrieve repository {o}/{r}'.format(
                    o=owner,
                    r=name,
                ),
                nfe
            )


class UpgradePullRequest(object):
    def __init__(self,
            pull_request,
            from_ref: DependencyBase,
            to_ref: DependencyBase,
        ):
        self.pull_request = ci.util.not_none(pull_request)

        if from_ref.name() != to_ref.name():
            raise ValueError('reference names do not match')
        if from_ref.type_name() != to_ref.type_name():
            raise ValueError('reference type names do not match')

        self.ref_name = from_ref.name()

        self.from_ref = from_ref
        self.to_ref = to_ref
        self.reference_type_name = from_ref.type_name()

    def is_obsolete(self, reference_component):
        '''returns a boolean indicating whether or not this Upgrade PR is "obsolete"

        A Upgrade is considered to be obsolete, iff the following conditions hold true:
        - the reference product contains a component reference with the same name
        - the destination version is greater than the greatest reference component version
        '''
        # find matching component versions
        reference_refs = sorted(
            [
                rc for rc in
                reference_component.dependencies().references(type_name=self.reference_type_name)
                if rc.name() == self.ref_name
            ],
            key=lambda r: version.parse_to_semver(r.version())
        )
        if not reference_refs:
            return False # special case: we have a new reference

        # sorted will return the greatest version last
        greatest_reference_version = version.parse_to_semver(reference_refs[-1].version())

        # PR is obsolete if same or newer component version is already configured in reference
        return greatest_reference_version >= version.parse_to_semver(self.to_ref.version())

    def target_matches(self, reference: DependencyBase):
        ci.util.check_type(reference, DependencyBase)

        if reference.type_name() != self.reference_type_name:
            return False
        if reference.name() != self.ref_name:
            return False
        if reference.version() != self.to_ref.version():
            return False

        return True

    def purge(self):
        self.pull_request.close()
        head_ref = 'heads/' + self.pull_request.head.ref
        self.pull_request.repository.ref(head_ref).delete()


class PullRequestUtil(RepositoryHelperBase):
    PR_TITLE_PATTERN = re.compile(r'^\[ci:(\S*):(\S*):(\S*)->(\S*)\]$')

    @staticmethod
    def calculate_pr_title(
            reference: DependencyBase,
            from_version: str,
            to_version: str,
    ) -> str:
        return '[ci:{tn}:{rn}:{fv}->{tv}]'.format(
            tn=reference.type_name(),
            rn=reference.name(),
            fv=from_version,
            tv=to_version,
        )

    def _has_upgrade_pr_title(self, pull_request) -> bool:
        return bool(self.PR_TITLE_PATTERN.fullmatch(pull_request.title))

    def _pr_to_upgrade_pull_request(self, pull_request):
        ci.util.not_none(pull_request)

        match = self.PR_TITLE_PATTERN.fullmatch(pull_request.title)
        if match is None:
            raise ValueError("PR-title '{t}' did not match title-schema".format(
                t=pull_request.title)
            )

        reference_type_name = match.group(1)
        if not reference_type_name:
            # backwards compatibility hack
            reference_type_name = 'component'

        reference_type = product.model.reference_type(reference_type_name)

        ref_name = match.group(2)
        from_version = match.group(3)
        to_version = match.group(4)

        from_ref = reference_type.create(name=ref_name, version=from_version)
        to_ref = reference_type.create(name=ref_name, version=to_version)

        return UpgradePullRequest(
            pull_request=pull_request,
            from_ref=from_ref,
            to_ref=to_ref,
        )

    def enumerate_upgrade_pull_requests(self, state_filter: str='open'):
        '''returns a sequence of `UpgradePullRequest` for all found pull-requests

        @param state_filter: all|open|closed (as defined by github api)
        '''
        def pr_to_upgrade_pr(pull_request):
            try:
                return self._pr_to_upgrade_pull_request(pull_request=pull_request)
            except product.model.InvalidComponentReferenceError:
                if pull_request.state == 'closed':
                    # silently ignore invalid component names in "old" PRs
                    pass
                else:
                    raise

        parsed_prs = ci.util.FluentIterable(self.repository.pull_requests(state=state_filter)) \
            .filter(self._has_upgrade_pr_title) \
            .map(pr_to_upgrade_pr) \
            .filter(lambda e: e) \
            .as_list()
        return parsed_prs

    def retrieve_pr_template_text(self):
        '''Return the content for the PR template file looking in predefined directories.
        If no template is found None is returned.
        '''
        pattern = re.compile(r"(pull_request_template)(\..{1,3})?$")
        directories = ['.github', '.', 'docs']
        for directory in directories:
            try:
                for filename, content in self.repository.directory_contents(directory):
                    if pattern.match(filename):
                        content.refresh()
                        return content.decoded.decode('utf-8')
            except github3.exceptions.NotFoundError:
                pass  # directory does not exists

        return None


class GitHubRepositoryHelper(RepositoryHelperBase):
    def create_or_update_file(
        self,
        file_path: str,
        file_contents: str,
        commit_message: str,
        branch: str=None,
    ) -> str:
        if branch is None:
            branch = self.default_branch

        try:
            contents = self.retrieve_file_contents(file_path=file_path, branch=branch)
        except NotFoundError:
            contents = None # file did not yet exist

        if contents:
            decoded_contents = contents.decoded.decode('utf-8')
            if decoded_contents == file_contents:
                # Nothing to do
                return ci.util.info(
                    'Repository file contents are identical to passed file contents.'
                )
            else:
                response = contents.update(
                    message=commit_message,
                    content=file_contents.encode('utf-8'),
                    branch=branch,
                )
        else:
            response = self.repository.create_file(
                path=file_path,
                message=commit_message,
                content=file_contents.encode('utf-8'),
                branch=branch,
            )
        return response['commit'].sha

    @staticmethod
    def from_githubrepobranch(
        githubrepobranch: GitHubRepoBranch,
    ):
        return GitHubRepositoryHelper(
            github_cfg=githubrepobranch.github_config(),
            owner=githubrepobranch.repo_owner(),
            name=githubrepobranch.repo_name(),
            default_branch=githubrepobranch.branch(),
        )

    def retrieve_file_contents(self, file_path: str, branch: str=None):
        if branch is None:
            branch = self.default_branch

        return self.repository.file_contents(
            path=file_path,
            ref=branch,
        )

    def retrieve_text_file_contents(
        self,
        file_path: str,
        branch: str=None,
        encoding: str='utf-8',
    ):
        if branch is None:
            branch = self.default_branch

        contents = self.retrieve_file_contents(file_path, branch)
        return contents.decoded.decode(encoding)

    def create_tag(
        self,
        tag_name: str,
        tag_message: str,
        repository_reference: str,
        author_name: str,
        author_email: str,
        repository_reference_type: str='commit'
    ):
        author = {
            'name': author_name,
            'email': author_email,
            'date': datetime.datetime.now(datetime.timezone.utc)
                    .strftime(self.GITHUB_TIMESTAMP_UTC_FORMAT)
        }
        self.repository.create_tag(
            tag=tag_name,
            message=tag_message,
            sha=repository_reference,
            obj_type=repository_reference_type,
            tagger=author
        )

    def create_release(
        self,
        tag_name: str,
        body: str,
        draft: bool=False,
        prerelease: bool=False,
        name: str=None
    ):
        release = self.repository.create_release(
            tag_name=tag_name,
            body=body,
            draft=draft,
            prerelease=prerelease,
            name=name
        )
        return release

    def delete_releases(
        self,
        release_names: [str],
    ):
        for release in self.repository.releases():
            if release.name in release_names:
                release.delete()

    def create_draft_release(
        self,
        name: str,
        body: str,
    ):
        return self.create_release(
            tag_name='',
            name=name,
            body=body,
            draft=True,
        )

    def update_release_notes(
        self,
        tag_name: str,
        body: str,
    ) -> bool:
        ci.util.not_empty(tag_name)
        release = self.repository.release_from_tag(tag_name)
        if not release:
            raise RuntimeError(
                f"No release with tag '{tag_name}' found "
                f"in repository {self.repository}"
            )
        return release.edit(body=body)

    def draft_release_with_name(
        self,
        name: str
    ) -> Release:
        releases = list(self.repository.releases())
        release = _.find(releases, lambda rls: rls.draft and rls.name == name)
        return release

    def tag_exists(
        self,
        tag_name: str,
    ):
        ci.util.not_empty(tag_name)
        try:
            self.repository.ref('tags/' + tag_name)
            return True
        except NotFoundError:
            return False

    def retrieve_asset_contents(self, release_tag: str, asset_label: str):
        ci.util.not_none(release_tag)
        ci.util.not_none(asset_label)

        release = self.repository.release_from_tag(release_tag)
        for asset in release.assets():
            if asset.label == asset_label or asset.name == asset_label:
                break
        else:
            response = requests.Response()
            response.status_code = 404
            response.json = lambda: {'message':'no asset with label {} found'.format(asset_label)}
            raise NotFoundError(resp=response)

        buffer = io.BytesIO()
        asset.download(buffer)
        return buffer.getvalue().decode()

    def release_versions(self):
        for tag_name in self.release_tags():
            try:
                version.parse_to_semver(tag_name)
                yield tag_name
                # XXX should rather return a "Version" object, containing both parsed and original
            except ValueError:
                pass # ignore

    def release_tags(self):
        return _ \
            .chain(self.repository.releases()) \
            .filter(lambda release: not release.draft and not release.prerelease) \
            .map('tag_name') \
            .filter(lambda tag: tag is not None) \
            .value()

    def search_issues_in_repo(self, query: str):
        query = "repo:{org}/{repo} {query}".format(
            org=self.owner,
            repo=self.repository_name,
            query=query
        )
        search_result = self.github.search_issues(query)
        return search_result

    def is_pr_created_by_org_member(self, pull_request_number):
        pull_request = self.repository.pull_request(pull_request_number)
        user_login = pull_request.user.login
        return self.is_org_member(self.owner, user_login)

    def add_labels_to_pull_request(self, pull_request_number, *labels):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.issue().add_labels(*labels)

    def is_org_member(self, organization_name, user_login):
        organization = self.github.organization(organization_name)
        return organization.is_member(user_login)

    def delete_outdated_draft_releases(self) -> Iterable[Tuple[github3.repos.release.Release, bool]]:
        '''Find outdated draft releases and try to delete them

        Yields tuples containing a release and a boolean indicating whether its deletion was
        successful.

        A draft release is considered outdated iff:
        1: its version is smaller than the greatest release version (according to semver) AND
            2a: it is NOT a hotfix draft release AND
            2b: there are no hotfix draft releases with the same major and minor version
            OR
            3a: it is a hotfix draft release AND
            3b: there is a hotfix draft release of greater version (according to semver)
                with the same major and minor version
        '''

        releases = [release for release in self.repository.releases()]
        non_draft_releases = [release for release in releases if not release.draft]
        draft_releases = [release for release in releases if release.draft]
        greatest_release_version = find_greatest_github_release_version(non_draft_releases)

        draft_releases_to_delete = outdated_draft_releases(
                draft_releases=draft_releases,
                greatest_release_version=greatest_release_version,
        )

        for release in draft_releases_to_delete:
            yield release, release.delete()


@deprecated.deprecated
def github_cfg_for_hostname(cfg_factory, host_name, require_labels=('ci',)): # XXX unhardcode label
    return ccc.github.github_cfg_for_hostname(
        host_name=host_name,
        cfg_factory=cfg_factory,
        require_labels=require_labels,
    )


@deprecated.deprecated
def _create_github_api_object(github_cfg):
    return ccc.github.github_api(github_cfg=github_cfg)


def branches(
    github_cfg,
    repo_owner: str,
    repo_name: str,
):
    github_api = ccc.github.github_api(github_cfg=github_cfg)
    repo = github_api.repository(repo_owner, repo_name)
    return list(map(lambda r: r.name, repo.branches()))


def retrieve_email_addresses(
    github_cfg: GithubConfig,
    github_users: [str],
    out_file: str=None
):
    github = ccc.github.github_api(github_cfg=github_cfg)

    def retrieve_email(username: str):
        user = github.user(username)
        return user.email

    fh = open(out_file, 'w') if out_file else sys.stdout

    email_addresses_count = 0

    for email_address in filter(None, map(retrieve_email, github_users)):
        fh.write(email_address + '\n')
        email_addresses_count += 1

    ci.util.verbose('retrieved {sc} email address(es) from {uc} user(s)'.format(
        sc=email_addresses_count,
        uc=len(github_users)
    )
    )


def _create_team(
    github: GitHub,
    organization_name: str,
    team_name: str
):
    # passed GitHub object must have org. admin authorization to create a team
    organization = github.organization(organization_name)
    team = _retrieve_team_by_name_or_none(organization, team_name)
    if team:
        ci.util.verbose("Team {name} already exists".format(name=team_name))
        return

    try:
        organization.create_team(name=team_name)
        ci.util.info("Team {name} created".format(name=team_name))
    except ForbiddenError as err:
        ci.util.fail("{err} Cannot create team {name} in org {org} due to missing privileges".format(
            err=err,
            name=team_name,
            org=organization_name
        ))


def _add_user_to_team(
    github: GitHub,
    organization_name: str,
    team_name: str,
    user_name: str
):
    # passed GitHub object must have org. admin authorization to add a user to a team
    organization = github.organization(organization_name)
    team = _retrieve_team_by_name_or_none(organization, team_name)
    if not team:
        ci.util.fail(f"Team '{team_name}' does not exist")

    if team.is_member(user_name):
        ci.util.verbose(f"'{user_name}' is already assigned to team '{team_name}'")
        return

    if team.add_member(username=user_name):
        ci.util.info(f"Added '{user_name}' to team '{team_name}'")
    else:
        ci.util.fail(
            f"Could not add '{user_name}' to team '{team_name}'. Check for missing privileges"
        )


def _add_all_repos_to_team(
    github: GitHub,
    organization_name: str,
    team_name: str,
    permission: RepoPermission=RepoPermission.ADMIN
):
    '''Add all repos found in `organization_name` to the given `team_name`'''
    # passed GitHub object must have org admin authorization to assign team to repo with admin rights
    organization = github.organization(organization_name)
    team = _retrieve_team_by_name_or_none(organization, team_name)
    if not team:
        ci.util.fail("Team {name} does not exist".format(name=team_name))

    for repo in organization.repositories():
        if team.has_repository(repo.full_name):
            ci.util.verbose("Team {teamnname} already assigned to repo {reponame}".format(
                teamnname=team_name,
                reponame=repo.full_name
            ))
            continue

        team.add_repository(repository=repo.full_name, permission=permission.value)
        ci.util.info("Added team {teamname} to repository {reponame}".format(
            teamname=team_name,
            reponame=repo.full_name
        ))


def _retrieve_team_by_name_or_none(
    organization: github3.orgs.Organization,
    team_name: str
) -> Team:

    team_list = list(filter(lambda t: t.name == team_name, organization.teams()))
    return team_list[0] if team_list else None


def find_greatest_github_release_version(
    releases: [github3.repos.release.Release],
):
    # currently, non-draft-releases are not created with a name by us. Use the tag name as fallback
    release_versions = [
        release.name if release.name else release.tag_name
        for release in releases
    ]

    def filter_non_semver_parseable_releases(release_name):
        try:
            version.parse_to_semver(release_name)
            return True
        except ValueError:
            ci.util.warning(f"Skipping release with non semver-parseable name {release_name}")
            return False

    release_versions = [
        name for name in filter(filter_non_semver_parseable_releases, release_versions)
    ]

    release_version_infos = [
        version.parse_to_semver(release_version)
        for release_version in release_versions
    ]
    return str(version.find_latest_version(release_version_infos))


def outdated_draft_releases(
    draft_releases: [github3.repos.release.Release],
    greatest_release_version: str,
):
    '''Find outdated draft releases from a list of draft releases and return them. This is achieved
    by partitioning the release versions according to their joined major and minor version.
    Partitions are then checked:
        - if there is only a single release in a partition it is either a hotfix release
            (keep corresponding release) or it is not (delete if it is not the greatest release
            according to semver)
        - if there are multiple releases versions in a partition, keep only the release
            corresponding to greatest (according to semver)
    '''

    greatest_release_version_info = version.parse_to_semver(greatest_release_version)

    def _has_semver_draft_prerelease_label(release_name):
        version_info = version.parse_to_semver(release_name)
        if version_info.prerelease != 'draft':
            return False
        return True

    autogenerated_draft_releases = [
        release for release in draft_releases
        if release.name
        and version.is_semver_parseable(release.name)
        and _has_semver_draft_prerelease_label(release.name)
    ]

    draft_release_version_infos = [
        version.parse_to_semver(release.name)
        for release in autogenerated_draft_releases
    ]

    def _yield_outdated_version_infos_from_partition(partition):
        if len(partition) == 1:
            version_info = partition.pop()
            if version_info < greatest_release_version_info and version_info.patch == 0:
                yield version_info
        else:
            yield from [
                version_info
                for version_info in partition[1:]
            ]

    outdated_version_infos = list()
    for partition in version.partition_by_major_and_minor(draft_release_version_infos):
        outdated_version_infos.extend(_yield_outdated_version_infos_from_partition(partition))

    outdated_draft_releases = [
        release
        for release in autogenerated_draft_releases
        if version.parse_to_semver(release.name) in outdated_version_infos
    ]

    return outdated_draft_releases
