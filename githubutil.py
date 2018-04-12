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

import util
import version

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
        contents = self.repository.file_contents(
            path=repository_version_file_path,
            ref=repository_branch
        )
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
    github_auth_token:str,
    github_url: str='https://github.com',
    github_verify_ssl:bool=False
):
    if github_url.strip('/') == 'https://github.com':
        github = GitHub(token=github_auth_token)
    else:
        github = GitHubEnterprise(url=github_url, token=github_auth_token, verify=github_verify_ssl)

    if not github:
        util.fail("Could not connect to GitHub-instance {url}".format(url=github_url))

    return github


def release_and_prepare_next_dev_cycle(
    github_url: str,
    github_auth_token:str,
    github_repository_owner: str,
    github_repository_name: str,
    repository_branch: str,
    repository_version_file_path: str,
    release_version: str,
    release_notes: str,
    version_operation: str="bump_minor",
    prerelease_suffix: str="dev",
    author_name: str="gardener-ci",
    author_email: str="gardener.ci.user@gmail.com",
    github_verify_ssl:bool=False
):
    # Do all the version handling upfront to catch errors early
    # Bump release version and add suffix
    next_version = version.process_version(
        version_str=release_version,
        operation=version_operation
    )
    next_version_dev = version.process_version(
        version_str=next_version,
        operation='set_prerelease',
        prerelease=prerelease_suffix
    )

    github = _create_github_api_object(
        github_url=github_url,
        github_auth_token=github_auth_token,
        github_verify_ssl=github_verify_ssl
    )

    helper = GitHubHelper(
        github=github,
        repository_owner=github_repository_owner,
        repository_name=github_repository_name,
    )

    # Persist version change, create release commit
    release_commit_sha = helper.create_or_update_file(
        repository_branch=repository_branch,
        repository_version_file_path=repository_version_file_path,
        file_contents=release_version,
        commit_message="Release " + release_version
    )
    helper.create_tag(
        tag_name=release_version,
        tag_message=release_notes,
        repository_reference=release_commit_sha,
        author_name=author_name,
        author_email=author_email
    )
    helper.repository.create_release(
      tag_name=release_version,
      body=release_notes,
      draft=False,
      prerelease=False
    )

    # Prepare version file for next dev cycle
    helper.create_or_update_file(
        repository_branch=repository_branch,
        repository_version_file_path=repository_version_file_path,
        file_contents=next_version_dev,
        commit_message="Prepare next dev cycle " + next_version_dev
    )


def retrieve_email_addresses(
    github_auth_token:str,
    github_users: [str],
    github_url: str='https://github.com',
    out_file: str=None
):
    github = _create_github_api_object(
        github_auth_token=github_auth_token,
        github_url=github_url,
        github_verify_ssl=False
    )
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

