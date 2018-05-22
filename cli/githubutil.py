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


