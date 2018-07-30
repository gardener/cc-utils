# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import enum
import functools
import io
import os
import re
import semver
import sys
import urllib.parse
from pydash import _

import requests

from github3.github import GitHub, GitHubEnterprise
from github3.repos.repo import Repository
from github3.repos.release import Release
from github3.exceptions import NotFoundError, ForbiddenError
from github3.orgs import Team

import util
import version

from http_requests import default_http_adapter
from product.model import ComponentReference
from model import ConfigFactory, GithubConfig

class RepoPermission(enum.Enum):
    PULL = "pull"
    PUSH = "push"
    ADMIN = "admin"


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
            self.github = _create_github_api_object(github_cfg)
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
        repository = self.github.repository(
                owner=owner,
                repository=name
        )
        return repository


class UpgradePullRequest(object):
    def __init__(self, pull_request, from_component, to_component):
        self.pull_request = util.not_none(pull_request)
        if from_component.name() != to_component.name():
            raise ValueError('component names do not match')
        self.component_name = from_component.name()

        self.from_component = from_component
        self.to_component = to_component

    def is_obsolete(self, reference_product):
        '''returns a boolean indicating whether or not this Upgrade PR is "obsolete"

        A Upgrade is considered to be obsolete, iff the following conditions hold true:
        - the reference product contains a component reference with the same name
        - the destination version is greater than the greatest reference component version
        '''
        # find matching component versions
        reference_components = sorted(
            [rc for rc in reference_product.components() if rc.name() == self.component_name],
            key=lambda c: semver.parse_version_info(c.version())
        )
        if not reference_components:
            return False # special case: we have a new component

        # sorted will return the greatest version last
        greatest_component_version = semver.parse_version_info(reference_components[-1].version())

        # PR is obsolete if same or newer component version is already configured in reference
        return greatest_component_version >= semver.parse_version_info(self.to_component.version())

    def target_matches(self, component_reference):
        return component_reference.name() == self.component_name and \
            component_reference.version() == self.to_component.version()

    def purge(self):
        self.pull_request.close()
        head_ref = 'heads/' + self.pull_request.head.ref
        self.pull_request.repository.ref(head_ref).delete()


class PullRequestUtil(RepositoryHelperBase):
    PR_TITLE_PATTERN = re.compile(r'^\[ci::(.*):(.*)->(.*)\]$')

    @staticmethod
    def calculate_pr_title(
            component_name: str,
            from_version: str,
            to_version: str,
    ) -> str:
        return '[ci::{cn}:{fv}->{tv}]'.format(
            cn=component_name,
            fv=from_version,
            tv=to_version,
        )

    def _has_upgrade_pr_title(self, pull_request)-> bool:
        return bool(self.PR_TITLE_PATTERN.fullmatch(pull_request.title))

    def _parse_pr_title(self, pull_request):
        util.not_none(pull_request)

        match = self.PR_TITLE_PATTERN.fullmatch(pull_request.title)
        if match is None:
            raise ValueError("PR-title '{t}' did not match title-schema".format(
                t=pull_request.title)
            )

        component_name = match.group(1)
        from_version = match.group(2)
        to_version = match.group(3)

        from_component_ref = ComponentReference.create(name=component_name, version=from_version)
        to_component_ref = ComponentReference.create(name=component_name, version=to_version)

        return UpgradePullRequest(
            pull_request=pull_request,
            from_component=from_component_ref,
            to_component=to_component_ref,
        )

    def enumerate_upgrade_pull_requests(self):
        '''returns a dictionary containing all (open) component ugprade pull requests

        {pull_request: ComponentUpgradeVector}
        '''
        parsed_prs = util.FluentIterable(self.repository.pull_requests()) \
            .filter(self._has_upgrade_pr_title) \
            .map(self._parse_pr_title) \
            .as_list()
        return parsed_prs


class GitHubRepositoryHelper(RepositoryHelperBase):
    def create_or_update_file(
        self,
        file_path: str,
        file_contents: str,
        commit_message: str,
        branch: str=None,
    )-> str:
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
                return util.info('Repository file contents are identical to passed file contents.')
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
            'date': datetime.datetime.now(datetime.timezone.utc).strftime(self.GITHUB_TIMESTAMP_UTC_FORMAT)
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

    def draft_release_with_name(
        self,
        name: str
    )->Release:
        releases = list(self.repository.releases())
        release = _.find(releases, lambda rls: rls.draft == True and rls.name == name)
        return release

    def tag_exists(
        self,
        tag_name: str,
    ):
        util.not_empty(tag_name)
        try:
            tag_ref = self.repository.ref('tags/' + tag_name)
            return True
        except NotFoundError:
            return False

    def retrieve_asset_contents(self, release_tag: str, asset_label: str):
        util.not_none(release_tag)
        util.not_none(asset_label)

        release = self.repository.release_from_tag(release_tag)
        for asset in release.assets():
            if asset.label == asset_label or asset.name == asset_label:
                break
        else:
            response = requests.Response()
            response.status_code = 404
            response.json = lambda: {'message':'no asset with label {l} found'.format(l=asset_label)}
            raise NotFoundError(resp=response)

        buffer = io.BytesIO()
        asset.download(buffer)
        return buffer.getvalue().decode()

    def release_versions(self):
        for release in self.repository.releases():
            try:
                yield semver.parse_version_info(release.tag_name)
            except ValueError: pass # ignore

    def release_tags(self):
        return _.map(self.repository.releases(), 'tag_name')

    def search_issues_in_repo(self, query: str):
        query = "repo:{org}/{repo} {query}".format(org=self.owner, repo=self.repository_name, query=query)
        search_result = self.github.search_issues(query)
        return search_result


def github_api_ctor(github_url: str, verify_ssl: bool=True):
    '''returns the appropriate github3.GitHub constructor for the given github URL

    In case github_url does not refer to github.com, the c'tor for GithubEnterprise is
    returned with the url argument preset, thus disburdening users to differentiate
    between github.com and non-github.com cases.
    '''
    parsed = urllib.parse.urlparse(github_url)
    if parsed.scheme:
        hostname = parsed.hostname
    else:
        raise ValueError('failed to parse url: ' + str(github_url))

    if hostname.lower() == 'github.com':
        return GitHub
    else:
        return functools.partial(GitHubEnterprise, url=github_url, verify=verify_ssl)


@functools.lru_cache()
def github_cfg_for_hostname(cfg_factory, host_name):
    util.not_none(host_name)
    for github_cfg in cfg_factory._cfg_elements(cfg_type_name='github'):
        if github_cfg.matches_hostname(host_name=host_name):
            return github_cfg
    raise RuntimeError('no github_cfg for {h}'.format(host_name))


@functools.lru_cache()
def _create_github_api_object(
    github_cfg: 'GithubConfig',
):
    github_url = github_cfg.http_url()
    github_auth_token = github_cfg.credentials().auth_token()

    verify_ssl = github_cfg.tls_validation()

    github_ctor = github_api_ctor(github_url=github_url, verify_ssl=verify_ssl)
    github_api = github_ctor(
        token=github_auth_token,
    )

    if not github_api:
        util.fail("Could not connect to GitHub-instance {url}".format(url=github_url))

    # patch github's requests.session to enable retrying when encountering sporadic errors. The requests library
    # sorts these adapters by prefix length, descending, and auto-inserts adapters for http and https. Therefore
    # we have to mount our default adapter explicitly to both.
    session = github_api.session
    session.mount('http://', default_http_adapter)
    session.mount('https://', default_http_adapter)

    return github_api


def branches(
    github_cfg,
    repo_owner: str,
    repo_name: str,
):
    github_api = _create_github_api_object(github_cfg=github_cfg)
    repo = github_api.repository(repo_owner, repo_name)
    return list(map(lambda r: r.name, repo.branches()))


def replicate_pipeline_definitions(
    definition_dir: str,
    cfg_dir: str,
    cfg_name: str,
):
    '''
    replicates pipeline definitions from cc-pipelines to component repositories.
    will only be required until definitions are moved to component repositories.
    '''
    util.existing_dir(definition_dir)
    util.existing_dir(cfg_dir)

    cfg_factory = ConfigFactory.from_cfg_dir(cfg_dir)
    cfg_set = cfg_factory.cfg_set(cfg_name)
    github_cfg = cfg_set.github()

    repo_mappings = util.parse_yaml_file(os.path.join(definition_dir, '.repository_mapping'))

    for repo_path, definition_file in repo_mappings.items():
        # hack: definition_file is a list with always exactly one entry
        definition_file = util.existing_file(os.path.join(definition_dir, definition_file[0]))
        with open(definition_file) as f:
            definition_contents = f.read()

        repo_owner, repo_name = repo_path.split('/')

        helper = GitHubRepositoryHelper(
            github_cfg=github_cfg,
            owner=repo_owner,
            name=repo_name,
        )
        # only do this for branch 'master' to avoid merge conflicts
        for branch_name in ['master']: #branches(github_cfg, repo_owner, repo_name):
            util.info('Replicating pipeline-definition: {r}:{b}'.format(
                    r=repo_path,
                    b=branch_name,
                )
            )
            # create pipeline definition file in .ci/pipeline_definitions
            try:
                helper.create_or_update_file(
                    branch=branch_name,
                    file_path='.ci/pipeline_definitions',
                    file_contents=definition_contents,
                    commit_message="Import cc-pipeline definition"
                )
            except:
                pass # keep going


def retrieve_email_addresses(
    github_cfg: GithubConfig,
    github_users: [str],
    out_file: str=None
):
    github = _create_github_api_object(github_cfg=github_cfg)
    def retrieve_email(username: str):
        user = github.user(username)
        return user.email

    fh = open(out_file, 'w') if out_file else sys.stdout

    email_addresses_count = 0

    for email_address in filter(None, map(retrieve_email, github_users)):
        fh.write(email_address + '\n')
        email_addresses_count += 1

    util.verbose('retrieved {sc} email address(es) from {uc} user(s)'.format(
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
        util.verbose("Team {name} already exists".format(name=team_name))
        return

    try:
        organization.create_team(name=team_name)
        util.info("Team {name} created".format(name=team_name))
    except ForbiddenError as err:
        util.fail("{err} Cannot create team {name} in org {org} due to missing privileges".format(
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
        util.fail("Team {name} does not exist".format(name=team_name))

    if team.is_member(user_name):
        util.verbose("{username} is already assigned to team {teamname}".format(
            username=user_name,
            teamname=team_name
        ))
        return

    if team.add_member(username=user_name):
        util.info("Added {username} to team {teamname}".format(
            username=user_name,
            teamname=team_name
        ))
    else:
        util.fail("Could not add {username} to team {teamname}. Please check for missing privileges".format(
            username=user_name,
            teamname=team_name
        ))


def _add_all_repos_to_team(
    github: GitHub,
    organization_name: str,
    team_name: str,
    permission: RepoPermission=RepoPermission.ADMIN
):
    '''Add all repos found in 'organization_name' to the given 'team_name'. Default permission is 'admin' '''
    # passed GitHub object must have org. admin authorization to assign team to repo with admin rights
    organization = github.organization(organization_name)
    team = _retrieve_team_by_name_or_none(organization, team_name)
    if not team:
        util.fail("Team {name} does not exist".format(name=team_name))

    for repo in organization.repositories():
        if team.has_repository(repo.full_name):
            util.verbose("Team {teamnname} already assigned to repo {reponame}".format(
                teamnname=team_name,
                reponame=repo.full_name
            ))
            continue

        team.add_repository(repository=repo.full_name, permission=permission.value)
        util.info("Added team {teamname} to repository {reponame}".format(
            teamname=team_name,
            reponame=repo.full_name
        ))


def _retrieve_team_by_name_or_none(
    organization: 'github3.orgs.Organization',
    team_name: str
) -> Team:

    team_list = list(filter(lambda t: t.name == team_name, organization.teams()))
    return team_list[0] if team_list else None
