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
import functools
import io
import os
import sys
from enum import Enum

from github3.github import GitHub, GitHubEnterprise
from github3.repos.repo import Repository
from github3.exceptions import NotFoundError, ForbiddenError
from github3.orgs import Team

import util
import version
from model import ConfigFactory, GithubConfig


class RepoPermission(Enum):
    PULL = "pull"
    PUSH = "push"
    ADMIN = "admin"


class GitHubRepositoryHelper(object):
    GITHUB_TIMESTAMP_UTC_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

    def __init__(
        self,
        github_cfg: GithubConfig,
        owner: str,
        name: str,
        default_branch: str='master',
    ):
        self.github = _create_github_api_object(github_cfg)
        self.repository = self._create_repository(
            owner=owner,
            name=name
        )
        self.default_branch = default_branch

    def _create_repository(self, owner: str, name: str):
        repository = self.github.repository(
                owner=owner,
                repository=name
        )
        return repository

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
    ):
        release = self.repository.create_release(
            tag_name=tag_name,
            body=body,
            draft=draft,
            prerelease=prerelease,
        )
        return release

    def retrieve_asset_contents(self, release_tag: str, asset_label: str):
        util.not_none(release_tag)
        util.not_none(asset_label)

        release = self.repository.release_from_tag(release_tag)
        for asset in release.assets():
            if asset.label == asset_label:
                break
        else:
            raise ValueError('no asset with label {l} found'.format(l=asset_label))

        buffer = io.BytesIO()
        asset.download(buffer)
        return buffer.getvalue().decode()


@functools.lru_cache()
def _create_github_api_object(
    github_cfg: 'GithubConfig',
):
    github_url = github_cfg.http_url()
    github_auth_token = github_cfg.credentials().auth_token()

    github_verify_ssl = github_cfg.tls_validation()

    if github_url.strip('/') == 'https://github.com':
        github = GitHub(token=github_auth_token)
    else:
        github = GitHubEnterprise(url=github_url, token=github_auth_token, verify=github_verify_ssl)

    if not github:
        util.fail("Could not connect to GitHub-instance {url}".format(url=github_url))

    return github


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
    util.ensure_directory_exists(definition_dir)
    util.ensure_directory_exists(cfg_dir)

    cfg_factory = ConfigFactory.from_cfg_dir(cfg_dir)
    cfg_set = cfg_factory.cfg_set(cfg_name)
    github_cfg = cfg_set.github()

    repo_mappings = util.parse_yaml_file(os.path.join(definition_dir, '.repository_mapping'))

    for repo_path, definition_file in repo_mappings.items():
        # hack: definition_file is a list with always exactly one entry
        definition_file = util.ensure_file_exists(os.path.join(definition_dir, definition_file[0]))
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
