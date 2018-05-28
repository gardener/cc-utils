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

import version
from urllib.parse import urlparse, parse_qs
from github3.exceptions import NotFoundError

from util import ctx, not_empty, info, warning, verbose
from github import GithubWebHookSyncer, CONCOURSE_ID
from github.util import GitHubHelper, _create_github_api_object

def release_and_prepare_next_dev_cycle(
    github_cfg_name: str,
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
):
    # retrieve github-cfg from secrets-server
    from config import _retrieve_model_element
    github_cfg = _retrieve_model_element(cfg_type='github', cfg_name=github_cfg_name)

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

    github = _create_github_api_object(github_cfg=github_cfg)

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

def remove_webhooks(
    github_org_name: str,
    github_cfg_name: str,
    concourse_cfg_name: str,
):
    '''
    Remove all webhooks which belong to the given concourse_cfg_name
    '''
    not_empty(github_org_name)
    not_empty(github_cfg_name)
    not_empty(concourse_cfg_name)

    cfg_factory = ctx().cfg_factory()
    github_cfg = cfg_factory.github(github_cfg_name)

    github_api = _create_github_api_object(github_cfg=github_cfg)
    github_org = github_api.organization(github_org_name)
    webhook_syncer = GithubWebHookSyncer(github_api)

    def filter_function(url):
        concourse_id = parse_qs(urlparse(url).query).get(CONCOURSE_ID)
        should_delete = concourse_id and concourse_cfg_name in concourse_id
        return should_delete

    for repository in github_org.repositories():
        removed = 0
        try:
            _, removed = webhook_syncer.remove_outdated_hooks(
                owner=github_org_name,
                repository_name=repository.name,
                urls_to_keep=[],
                url_filter_fun=filter_function
            )
        except NotFoundError as err:
            warning("{msg}. Please check privileges for repository {repo}".format(
                msg=err,
                repo=repository.name)
        )

        if removed > 0:
            info("Removed {num} webhook from repository {repo}".format(num=removed, repo=repository.name))
        else:
            verbose("Nothing to do for repository {repo}".format(repo=repository.name))
