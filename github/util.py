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

import collections
import datetime
import deprecated
import enum
import io
import re
import sys

import typing
from typing import Iterable, Tuple
from pydash import _

import requests

import github3
import github3.issues
from github3.exceptions import NotFoundError
from github3.github import GitHub
from github3.orgs import Team
from github3.pulls import PullRequest
from github3.repos.release import Release

import gci.componentmodel
import gci.componentmodel as cm
import ccc.github
import ci.util
import product.v2
import version

from model.github import GithubConfig


class RepoPermission(enum.Enum):
    PULL = "pull"
    PUSH = "push"
    ADMIN = "admin"


class GitHubRepoBranch:
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


class RepositoryHelperBase:
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


class UpgradePullRequest:
    def __init__(
        self,
        pull_request: PullRequest,
        from_ref: typing.Union[cm.Resource, cm.ComponentReference],
        to_ref: typing.Union[cm.Resource, cm.ComponentReference],
    ):
        self.pull_request = ci.util.not_none(pull_request)

        if from_ref.name != to_ref.name:
            raise ValueError(f'reference name mismatch {from_ref.name=} {to_ref.name=}')
        if (isinstance(from_ref, cm.Resource) and isinstance(to_ref, cm.Resource) and
            from_ref.type != to_ref.type
            ) or \
            type(from_ref) != type(to_ref):
            raise ValueError(f'reference types do not match: {from_ref=} {to_ref=}')

        self.ref_name = from_ref.name

        self.from_ref = from_ref
        self.to_ref = to_ref
        if isinstance(from_ref, cm.Resource):
            if isinstance(from_ref.type, enum.Enum):
                self.reference_type_name = from_ref.type.value
            elif isinstance(from_ref.type, str):
                self.reference_type_name = from_ref.type
            else:
                raise ValueError(from_ref.type)
        elif isinstance(from_ref, cm.ComponentReference):
            self.reference_type_name = product.v2.COMPONENT_TYPE_NAME
        else:
            raise NotImplementedError(from_ref.type)

    def is_obsolete(
        self,
        reference_component: gci.componentmodel.Component,
    ):
        '''returns a boolean indicating whether or not this Upgrade PR is "obsolete"

        A Upgrade is considered to be obsolete, iff the following conditions hold true:
        - the reference product contains a component reference with the same name
        - the destination version is greater than the greatest reference component version
        '''
        # find matching component versions
        if not isinstance(reference_component, gci.componentmodel.Component):
            raise TypeError(reference_component)

        if self.reference_type_name == product.v2.COMPONENT_TYPE_NAME:
            reference_refs = sorted(
                [
                    rc for rc in reference_component.componentReferences
                    if rc.componentName == self.ref_name
                ],
                key=lambda r: version.parse_to_semver(r.version)
            )

            if not reference_refs:
                return False # special case: we have a new reference

            greatest_reference_version = version.parse_to_semver(reference_refs[-1].version)

        else:
            raise NotImplementedError

        # PR is obsolete if same or newer component version is already configured in reference
        return greatest_reference_version >= version.parse_to_semver(self.to_ref.version)

    def target_matches(
        self,
        reference: typing.Tuple[cm.ComponentReference, cm.Resource],
        reference_version: str = None,
    ):
        if not isinstance(reference, cm.ComponentReference) and not \
                isinstance(reference, cm.Resource):
            raise TypeError(reference)

        if isinstance(reference, cm.ComponentReference):
            if product.v2.COMPONENT_TYPE_NAME != self.reference_type_name:
                return False
            if reference.componentName != self.ref_name:
                return False
        else: # cm.Resource, already checked above
            if reference.name != self.ref_name:
                return False
            if isinstance(reference.type, enum.Enum):
                reference_type = reference.type.value
            elif isinstance(reference.type, str):
                reference_type = reference.type
            else:
                raise ValueError(reference.type)
            if reference_type != self.reference_type_name:
                return False

        reference_version = reference_version or reference.version
        if reference_version != self.to_ref.version:
            return False

        return True

    def purge(self):
        self.pull_request.close()
        head_ref = 'heads/' + self.pull_request.head.ref
        self.pull_request.repository.ref(head_ref).delete()


def iter_obsolete_upgrade_pull_requests(
    upgrade_pull_requests: typing.Iterable[UpgradePullRequest],
    keep_hotfix_versions: bool=True,
) -> typing.Generator[UpgradePullRequest, None, None]:
    grouped_upgrade_pull_requests = collections.defaultdict(list)

    def group_name(upgrade_pull_request: UpgradePullRequest):
        '''
        calculate groupname, depending on whether or not we should keep hotfix_versions;
        for each upgrade-pr-group, we keep only exactly one version (the greatest tgt-version);
        therefore, to prevent hotfix-upgrades from being removed, collect hotfixes in a separate
        group.
        '''
        cname = upgrade_pull_request.to_ref.componentName

        if not keep_hotfix_versions:
            return cname

        from_version = version.parse_to_semver(upgrade_pull_request.from_ref.version)
        to_version = version.parse_to_semver(upgrade_pull_request.to_ref.version)

        if from_version.major != to_version.major:
            return cname # not a hotfix
        if from_version.minor != to_version.minor:
            return cname # not a hotfix (hardcode hotfixes differ at patchlevel, always)

        # we have a hotfix version (patchlevel differs)
        return f'{cname}:{from_version.major}.{from_version.minor}'

    for upgrade_pull_request in upgrade_pull_requests:
        if upgrade_pull_request.pull_request.state != 'open':
            continue
        name = group_name(upgrade_pull_request)
        grouped_upgrade_pull_requests[name].append(upgrade_pull_request)

    for upgrade_pull_request_group in grouped_upgrade_pull_requests.values():
        if len(upgrade_pull_request_group) < 2:
            continue

        # greatest version will be sorted as last element
        ordered_by_version = sorted(
            upgrade_pull_request_group,
            key=lambda upr: version.parse_to_semver(upr.to_ref.version),
        )

        greatest_version = version.parse_to_semver(ordered_by_version[-1].to_ref.version)
        for upgrade_pr in ordered_by_version:
            if version.parse_to_semver(upgrade_pr.to_ref.version) < greatest_version:
                yield upgrade_pr


class PullRequestUtil(RepositoryHelperBase):
    PR_TITLE_PATTERN = re.compile(r'^\[ci:(\S*):(\S*):(\S*)->(\S*)\]$')

    @staticmethod
    def calculate_pr_title(
            reference: gci.componentmodel.ComponentReference,
            from_version: str,
            to_version: str,
    ) -> str:
        if not isinstance(reference, gci.componentmodel.ComponentReference):
            raise TypeError(reference)

        type_name = product.v2.COMPONENT_TYPE_NAME
        reference_name = reference.componentName

        return f'[ci:{type_name}:{reference_name}:{from_version}->{to_version}]'

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

        if not reference_type_name == 'component':
            raise NotImplementedError(reference_type_name) # todo: support all resources

        ref_name = match.group(2)
        from_version = match.group(3)
        to_version = match.group(4)

        from_ref = cm.ComponentReference(
            name=ref_name,
            componentName=ref_name,
            version=from_version,
        )
        to_ref = cm.ComponentReference(
            name=ref_name,
            componentName=ref_name,
            version=to_version,
        )

        return UpgradePullRequest(
            pull_request=pull_request,
            from_ref=from_ref,
            to_ref=to_ref,
        )

    def enumerate_upgrade_pull_requests(
        self,
        state: str='all',
    ):
        '''returns a sequence of `UpgradePullRequest` for all found pull-requests
        '''
        def pr_to_upgrade_pr(pull_request):
            return self._pr_to_upgrade_pull_request(pull_request=pull_request)

        def strip_title(pull_request):
            pull_request.title = pull_request.title.strip()
            return pull_request

        parsed_prs = ci.util.FluentIterable(
            self.repository.pull_requests(
                state=state,
                number=128, # avoid issueing more than one github-api-request
            )
        ) \
            .map(strip_title) \
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
                pass  # directory does not exist

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

    MAXIMUM_GITHUB_RELEASE_BODY_LENGTH = 25000
    '''The limit is not documented explicitly in the GitHub docs. To see it, the error returned by
    GitHub when creating a release with more then the allowed number of characters must be
    looked at.'''

    def _replacement_release_notes(
        self,
        asset_url: str,
        component_name: str,
        component_version: str,
    ):
        return (
            f'The release-notes for component **{component_name}** in version '
            f'**{component_version}** exceeded the maximum length of '
            f'{self.MAXIMUM_GITHUB_RELEASE_BODY_LENGTH} characters allowed by GitHub for '
            'release-bodies.\n'
            f'They have been uploaded as release-asset and can be found at {asset_url}.'
        )

    RELEASE_NOTES_ASSET_NAME = 'release_notes.md'

    def create_release(
        self,
        tag_name: str,
        body: str,
        draft: bool=False,
        prerelease: bool=False,
        name: str=None,
        component_name: str=None,
        component_version: str=None,
    ):
        if len(body) < self.MAXIMUM_GITHUB_RELEASE_BODY_LENGTH:
            return self.repository.create_release(
                tag_name=tag_name,
                body=body,
                draft=draft,
                prerelease=prerelease,
                name=name
            )
        else:
            # release notes are too large to be added to the github-release directly. As a work-
            # around, attach them to the release and write an appropriate release-body pointing
            # towards the attached asset.
            # For draft releases, the url cannot be calculated easily beforehand, so create the
            # release, attach the notes and then retrieve the URL via the asset-object.
            release = self.repository.create_release(
                tag_name=tag_name,
                body='',
                draft=draft,
                prerelease=prerelease,
                name=name
            )
            release_notes_asset = release.upload_asset(
                content_type='text/markdown',
                name=self.RELEASE_NOTES_ASSET_NAME,
                asset=body.encode('utf-8'),
                label='Release Notes',
            )
            release.edit(
                body=self._replacement_release_notes(
                    asset_url=release_notes_asset.browser_download_url,
                    component_name=component_name,
                    # Fallback: use tag_name if version not explicitly given
                    component_version=component_version or tag_name,
                )
            )
            return release

    def delete_releases(
        self,
        release_names: typing.Iterable[str],
    ):
        for release in self.repository.releases():
            if release.name in release_names:
                release.delete()

    def create_draft_release(
        self,
        name: str,
        body: str,
        component_name: str=None,
        component_version: str=None,
    ):
        return self.create_release(
            tag_name='',
            name=name,
            body=body,
            draft=True,
            component_name=component_name,
            component_version=component_version,
        )

    def promote_draft_release(
        self,
        draft_release,
        release_tag,
        release_version,
        component_name: str=None,
    ):
        draft_release.edit(
            tag_name=release_tag,
            body=None,
            draft=False,
            prerelease=False,
            name=release_version,
        )

        # If there is a release-notes asset attached, we need to update the release-notes after
        # promoting the release so that the contained URL is adjusted as well
        release_notes_asset = next(
            (a for a in draft_release.assets() if a.name == self.RELEASE_NOTES_ASSET_NAME),
            None,
        )
        if release_notes_asset:
            draft_release.edit(
                body=self._replacement_release_notes(
                    asset_url=release_notes_asset.browser_download_url,
                    component_name=component_name,
                    component_version=release_version,
                )
            )

    def update_release_notes(
        self,
        tag_name: str,
        component_name: str,
        body: str,
    ) -> bool:
        ci.util.not_empty(tag_name)

        release = self.repository.release_from_tag(tag_name)
        if not release:
            raise RuntimeError(
                f"No release with tag '{tag_name}' found "
                f"in repository {self.repository}"
            )

        if len(body) < self.MAXIMUM_GITHUB_RELEASE_BODY_LENGTH:
            release.edit(body=body)
        else:
            release_notes_asset = next(
                (a for a in release.assets() if a.name == self.RELEASE_NOTES_ASSET_NAME),
                None,
            )
            # Clean up any attached release-note-asset
            if release_notes_asset:
                release_notes_asset.delete()

            release_notes_asset = release.upload_asset(
                content_type='text/markdown',
                name=self.RELEASE_NOTES_ASSET_NAME,
                asset=body.encode('utf-8'),
                label='Release Notes',
            )
            release.edit(
                body=self._replacement_release_notes(
                    asset_url=release_notes_asset.browser_download_url,
                    component_version=tag_name,
                    component_name=component_name)
            )

        return release

    def draft_release_with_name(
        self,
        name: str
    ) -> Release:
        # if there are more than 1021 releases, github(.com) will return http-500 one requesting
        # additional releases. As this limit is typically not reached, hardcode limit for now
        # in _most_ cases, most recent releases are returned first, so this should hardly ever
        # be an actual issue
        max_releases = 1020
        for release in self.repository.releases(number=max_releases):
            if not release.draft:
                continue
            if release.name == name:
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
        query = f'repo:{self.owner}/{self.repository_name} {query}'
        search_result = self.github.search_issues(query)
        return search_result

    def is_pr_created_by_org_member(self, pull_request_number):
        pull_request = self.repository.pull_request(pull_request_number)
        user_login = pull_request.user.login
        return self.is_org_member(self.owner, user_login)

    def add_labels_to_pull_request(self, pull_request_number, *labels):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.issue().add_labels(*labels)

    def remove_label_from_pull_request(self, pull_request_number, label):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.issue().remove_label(label)

    def add_comment_to_pr(self, pull_request_number, comment):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.create_comment(comment)

    def is_org_member(self, organization_name, user_login):
        organization = self.github.organization(organization_name)
        return organization.is_member(user_login)

    def is_team_member(self, team_name, user_login) -> bool:
        '''Returns a bool indicating team-membership to the given team for the given user-login

        The team-name is expected in the format `<org-name>/<team-name>`.
        Note: If the team cannot be seen by the user used for the lookup or does not exist `False`
        will be returned.
        '''
        o, t = team_name.split('/')
        org = self.github.organization(o)
        try:
            team = org.team_by_name(t)
            team.membership_for(user_login)
        except github3.exceptions.NotFoundError:
            return False
        else:
            return True

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

        releases = [release for release in self.repository.releases(number=20)]
        non_draft_releases = [release for release in releases if not release.draft]
        draft_releases = [release for release in releases if release.draft]
        greatest_release_version = find_greatest_github_release_version(non_draft_releases)

        if greatest_release_version is not None:
            draft_releases_to_delete = outdated_draft_releases(
                    draft_releases=draft_releases,
                    greatest_release_version=greatest_release_version,
            )
        else:
            draft_releases_to_delete = []

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
    github_users: typing.Sequence[str] | typing.Collection[str],
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


def _retrieve_team_by_name_or_none(
    organization: github3.orgs.Organization,
    team_name: str
) -> Team:

    team_list = list(filter(lambda t: t.name == team_name, organization.teams()))
    return team_list[0] if team_list else None


def find_greatest_github_release_version(
    releases: typing.List[github3.repos.release.Release],
    warn_for_unparseable_releases: bool = True,
    ignore_prerelease_versions: bool = False,
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
            if warn_for_unparseable_releases:
                ci.util.warning(f'ignoring release {release_name=} (not semver)')
            return False

    release_versions = [
        name for name in filter(filter_non_semver_parseable_releases, release_versions)
    ]

    release_version_infos = [
        version.parse_to_semver(release_version)
        for release_version in release_versions
    ]
    latest_version = version.find_latest_version(
        versions=release_version_infos,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )
    if latest_version:
        return str(latest_version)
    else:
        return None


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


def close_issue(
    issue: github3.issues.ShortIssue,
) -> bool:
    '''
    handle known corner-cases where regular issue close will fail
    comment on issue if closing still fails

    returns True if close was successful, False otherwise
    '''

    def try_close() -> bool:
        try:
            return issue.close()

        except github3.exceptions.UnprocessableEntity:
            # likely that assignee was suspended from github

            if not issue.assignees:
                raise

            issue.remove_assignees(issue.assignees)
            return issue.close()

    closed = try_close()
    if not closed:
        issue.create_comment('unable to close ticket')

    return closed
