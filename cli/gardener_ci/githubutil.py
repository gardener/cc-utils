# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from ci.util import (
    ctx,
)
import github.release
import ccc.github


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
    github_api = ccc.github.github_api(github_cfg)

    repository = github_api.repository(
        owner=github_repository_owner,
        repository=github_repository_name,
    )

    if only_outdated:
        releases = [release for release in repository.releases()]
        non_draft_releases = [release for release in releases if not release.draft]
        greatest_release_version = github.release.find_greatest_github_release_version(
            non_draft_releases,
        )
    else:
        releases = repository.releases()

    draft_releases = [release for release in releases if release.draft]

    if only_outdated:
        if greatest_release_version is not None:
            draft_releases = github.release.outdated_draft_releases(
                draft_releases=draft_releases,
                greatest_release_version=greatest_release_version,
            )
        else:
            draft_releases = []
    for draft_release in draft_releases:
        print(draft_release.name)
