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
import urllib

from ci.util import (
    ctx,
)
from github.util import (
    GitHubRepositoryHelper,
    find_greatest_github_release_version,
    outdated_draft_releases,

)
import ccc.github

import github3


def list_draft_releases(
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
    only_outdated: bool = False,
):
    '''List all draft releases in a GitHub repository. If the `--only-outdated` flag is set,
    only outdated draft releases are printed. A draft release is considered outdated iff:
        1: its version is smaller than the greatest release version (according to semver) AND
            2a: it is NOT a hotfix draft release AND
            2b: there are no hotfix draft releases with the same major and minor version
            OR
            3a: it is a hotfix draft release AND
            3b: there is a hotfix draft release of greater version (according to semver)
                with the same major and minor version

    Hotfix draft release in this context are draft releases with a semver patch version that is
    not equal to 0.
    '''
    github_cfg = ctx().cfg_factory().github(github_cfg_name)
    github_helper = GitHubRepositoryHelper(
        owner=github_repository_owner,
        name=github_repository_name,
        github_cfg=github_cfg,
    )
    if only_outdated:
        releases = [release for release in github_helper.repository.releases()]
        non_draft_releases = [release for release in releases if not release.draft]
        greatest_release_version = find_greatest_github_release_version(non_draft_releases)
    else:
        releases = github_helper.repository.releases()

    draft_releases = [release for release in releases if release.draft]

    if only_outdated:
        if greatest_release_version is not None:
            draft_releases = outdated_draft_releases(
                draft_releases=draft_releases,
                greatest_release_version=greatest_release_version,
            )
        else:
            draft_releases = []
    for draft_release in draft_releases:
        print(draft_release.name)


def delete_releases(
    github_cfg_name: str,
    github_repository_owner: str,
    github_repository_name: str,
    release_name: [str],
):
    github_cfg = ctx().cfg_factory().github(github_cfg_name)
    github_helper = GitHubRepositoryHelper(
        owner=github_repository_owner,
        name=github_repository_name,
        github_cfg=github_cfg,
    )
    github_helper.delete_releases(release_names=release_name)


def greatest_release_version(
    github_repository_url: str,
    anonymous: bool=False,
    ignore_prereleases: bool=False,
):
    '''Find the release with the greatest name (according to semver) and print its semver-version.

    Note:
    - This will only consider releases whose names are either immediately parseable as semver-
    versions, or prefixed with a single character ('v').
    - The 'v'-prefix (if present) will be not be present in the output.
    - If a release has no name, its tag will be used instead of its name.

    For more details on the ordering of semantic versioning, see 'https://www.semver.org'.
    '''
    parse_result = urllib.parse.urlparse(github_repository_url)

    if not parse_result.netloc:
        raise ValueError(f'Could not determine host for github-url {github_repository_url}')
    host = parse_result.netloc

    try:
        path = parse_result.path.strip('/')
        org, repo = path.split('/')
    except ValueError as e:
        raise ValueError(f"Could not extract org- and repo-name. Error: {e}")

    if anonymous:
        if 'github.com' not in host:
            raise ValueError("Anonymous access is only possible for github.com")
        github_api = github3.GitHub()
        repo_helper = GitHubRepositoryHelper(owner=org, name=repo, github_api=github_api)

    else:
        repo_helper = ccc.github.repo_helper(host=host, org=org, repo=repo)

    print(
        find_greatest_github_release_version(
            releases=repo_helper.repository.releases(),
            warn_for_unparseable_releases=False,
            ignore_prerelease_versions=ignore_prereleases,
        )
    )
