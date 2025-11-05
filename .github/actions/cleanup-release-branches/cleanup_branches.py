#!/usr/bin/env python3

import argparse
import collections.abc
import os

import github3.repos.branch
import github3.repos.repo
import semver
import yaml

import github
import ocm.branch_info
import version


def iter_stale_release_branches(
    version_semver: semver.VersionInfo,
    branch_info: ocm.branch_info.BranchInfo,
    branches: collections.abc.Iterable[github3.repos.branch.Branch],
) -> collections.abc.Iterable[str]:
    '''
    Yields the names of stale release branches in a GitHub repository. A release branch is considered
    stale if its version (major, minor, or patch, according to `significant_part` in `branch_info`)
    is older than the current version by at least the `supported_versions_count`.
    '''
    major = version_semver.major
    minor = version_semver.minor
    patch = version_semver.patch

    for branch in branches:
        if not (match := branch_info.release_branch_pattern.fullmatch(branch.name)):
            print(f'INFO: Skipping non-release branch {branch.name}')
            continue

        print(f'INFO: Found release branch {branch.name}')

        groups = match.groupdict()
        branch_major = int(groups['major']) if 'major' in groups else None
        branch_minor = int(groups['minor']) if 'minor' in groups else None
        branch_patch = int(groups['patch']) if 'patch' in groups else None

        if branch_info.branch_policy.significant_part is ocm.branch_info.VersionParts.MAJOR:
            if branch_major is None:
                print(f'WARNING: Cannot parse {branch.name=}, failed to parse major version')
                continue

            is_stale = major - branch_major >= branch_info.branch_policy.supported_versions_count

        elif branch_info.branch_policy.significant_part is ocm.branch_info.VersionParts.MINOR:
            if branch_major is None or branch_minor is None:
                print(f'WARNING: Cannot parse {branch.name=}, failed to parse major/minor version')
                continue

            if major != branch_major:
                print(f'INFO: Skipping branch {branch.name}, major version mismatch')
                continue

            is_stale = minor - branch_minor >= branch_info.branch_policy.supported_versions_count

        elif branch_info.branch_policy.significant_part is ocm.branch_info.VersionParts.PATCH:
            if branch_major is None or branch_minor is None or branch_patch is None:
                print(f'WARNING: Cannot parse {branch.name=}, failed to parse version')
                continue

            if major != branch_major:
                print(f'INFO: Skipping branch {branch.name}, major version mismatch')
                continue

            if minor != branch_minor:
                print(f'INFO: Skipping branch {branch.name}, minor version mismatch')
                continue

            is_stale = patch - branch_patch >= branch_info.branch_policy.supported_versions_count

        else:
            raise ValueError(branch_info.branch_policy.significant_part)

        if not is_stale:
            continue

        print(f'INFO: Found stale release branch {branch.name}')
        yield branch.name


def delete_stale_release_branches(
    version_semver: semver.VersionInfo,
    branch_info: ocm.branch_info.BranchInfo,
    repo: github3.repos.repo.Repository,
):
    for stale_release_branch in iter_stale_release_branches(
        version_semver=version_semver,
        branch_info=branch_info,
        branches=repo.branches(),
    ):
        print(f'INFO: Deleting {stale_release_branch=}')

        repo.ref(f'heads/{stale_release_branch}').delete()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--version',
        required=True,
        help='Currently released version to use as reference for detecting stale release branches.',
    )
    parser.add_argument(
        '--branch-info-file',
        default='.ocm/branch-info.yaml',
        help='Path to the local `branch-info.yaml` file.',
    )

    parsed = parser.parse_args()

    if not os.path.isfile(branch_info_file := parsed.branch_info_file):
        print(f'ERROR: Did not find {branch_info_file=}')
        exit(1)

    with open(branch_info_file) as f:
        branch_info_raw = yaml.safe_load(f)

    branch_info = ocm.branch_info.BranchInfo.from_dict(branch_info_raw)

    github_api = github.github_api()
    _, owner, repository = github.host_org_and_repo()

    repo = github_api.repository(
        owner=owner,
        repository=repository,
    )

    version_semver = version.parse_to_semver(parsed.version)

    delete_stale_release_branches(
        version_semver=version_semver,
        branch_info=branch_info,
        repo=repo,
    )


if __name__ == '__main__':
    main()
