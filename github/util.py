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
import os
import sys

from github3.github import GitHub, GitHubEnterprise
from github3.repos.repo import Repository
from github3.exceptions import NotFoundError

import util
import version
from model import ConfigFactory, GithubConfig

class GitHubHelper(object):
    GITHUB_TIMESTAMP_UTC_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

    def __init__(
        self,
        github: GitHub,
        repository_owner: str,
        repository_name: str,
    ):
        self.github = github
        self.repository = self._create_repository(
            repository_owner=repository_owner,
            repository_name=repository_name
        )

    def _create_repository(
        self,
        repository_owner: str,
        repository_name: str
    ):
        repository = self.github.repository(
                owner=repository_owner,
                repository=repository_name
        )
        if not repository:
            util.fail("Could not retrieve repository {owner}/{name}".format(owner=repository_owner, name=repository_name))
        return repository

    def create_or_update_file(
        self,
        repository_branch: str,
        repository_version_file_path: str,
        file_contents: str,
        commit_message: str
    )-> str:
        try:
            contents = self.repository.file_contents(
                path=repository_version_file_path,
                ref=repository_branch
            )
        except NotFoundError:
            contents = None # file did not yet exist

        if contents:
            decoded_contents = contents.decoded.decode('utf-8')
            if decoded_contents == file_contents:
                # Nothing to do
                return util.info("Repository file contents are identical to passed file contents.")
            else:
                response = contents.update(commit_message, file_contents.encode('utf-8'), branch=repository_branch)
        else:
            response = self.repository.create_file(
                path=repository_version_file_path,
                message=commit_message,
                content=file_contents.encode('utf-8'),
                branch=repository_branch
            )
        if not response:
            util.fail('failed to update or create file (missing privileges?)')

        return response["commit"].sha

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
            "name": author_name,
            "email": author_email,
            "date": datetime.datetime.now(datetime.timezone.utc).strftime(self.GITHUB_TIMESTAMP_UTC_FORMAT)
        }
        self.repository.create_tag(
            tag=tag_name,
            message=tag_message,
            sha=repository_reference,
            obj_type=repository_reference_type,
            tagger=author
        )

    def retrieve_email_address(self, user_name):
        user = self.repository.user(user_name)
        if not user:
            util.fail('no such user: {u}'.format(u=user_name))
        return user


def _create_github_api_object(
    github_cfg: 'GithubConfig'
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

    github = _create_github_api_object(github_cfg=github_cfg)

    repo_mappings = util.parse_yaml_file(os.path.join(definition_dir, '.repository_mapping'))

    for repo_path, definition_file in repo_mappings.items():
        # hack: definition_file is a list with always exactly one entry
        definition_file = util.ensure_file_exists(os.path.join(definition_dir, definition_file[0]))
        with open(definition_file) as f:
            definition_contents = f.read()

        repo_owner, repo_name = repo_path.split('/')


        helper = GitHubHelper(
            github=github,
            repository_owner=repo_owner,
            repository_name=repo_name,
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
                    repository_branch=branch_name,
                    repository_version_file_path='.ci/pipeline_definitions',
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

