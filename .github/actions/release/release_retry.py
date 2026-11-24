# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
Resolve a published release tag without changing refs, and reuse it only if its commit
matches the imported release and GitHub considers its signature valid.
'''

import argparse
import os
import re
import subprocess

import requests


def git(*args: str, check: bool=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ('git', '--no-replace-objects', *args),
        check=check,
        capture_output=True,
        text=True,
    )


def commit_identity(oid: str) -> tuple[str, list[str]]:
    # Read the object itself: rev-list may hide parents at a shallow boundary.
    headers = git('cat-file', 'commit', oid).stdout.split('\n\n', 1)[0].splitlines()
    tree = next(line.removeprefix('tree ') for line in headers if line.startswith('tree '))
    parents = [line.removeprefix('parent ') for line in headers if line.startswith('parent ')]
    return tree, parents


def existing_release_commit(
    tag_ref: str,
    repository: str,
    token: str,
    api_url: str,
) -> str | None:
    if not tag_ref.startswith('refs/tags/'):
        raise ValueError(f'not a tag ref: {tag_ref!r}')
    git('check-ref-format', tag_ref)
    intended_tree, intended_parents = commit_identity('HEAD')
    if len(intended_parents) != 1:
        raise ValueError('expected the imported release to have exactly one parent')

    remote = git('ls-remote', '--exit-code', '--refs', 'origin', tag_ref, check=False)
    if remote.returncode == 2:
        return None  # only "no matching ref" means it is safe to create a new release
    remote.check_returncode()
    matches = [line.split('\t') for line in remote.stdout.splitlines()]
    if (
        len(matches) != 1
        or len(matches[0]) != 2
        or matches[0][1] != tag_ref
        or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', matches[0][0])
    ):
        raise ValueError(f'unexpected ls-remote result for {tag_ref}: {remote.stdout!r}')
    tag_oid = matches[0][0]
    # Fetch the observed object, not a mutable ref or a local tag with the same name.
    # No refspec destination and no auto-following tags: existing refs remain untouched.
    git('fetch', '--no-tags', '--no-write-fetch-head', 'origin', tag_oid)
    oid = git('rev-parse', '--verify', f'{tag_oid}^{{commit}}').stdout.strip()
    tree, parents = commit_identity(oid)
    if tree != intended_tree or parents != intended_parents:
        raise ValueError(
            f'published release {tag_ref} ({oid}) does not match the intended release: '
            f'tree {tree} != {intended_tree} or parents {parents} != {intended_parents}'
        )

    response = requests.get(
        f'{api_url.rstrip("/")}/repos/{repository}/git/commits/{oid}',
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
        },
        timeout=30,
    )
    response.raise_for_status()
    commit = response.json()
    verification = commit.get('verification') or {}
    if (
        commit.get('sha') != oid
        or verification.get('verified') is not True
        or verification.get('reason') != 'valid'
    ):
        raise ValueError(
            f'published release {tag_ref} ({oid}) is not a verified signed commit: '
            f'{verification.get("reason")!r}'
        )
    return oid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag-ref', required=True)
    parsed = parser.parse_args()
    oid = existing_release_commit(
        tag_ref=parsed.tag_ref,
        repository=os.environ['GITHUB_REPOSITORY'],
        token=os.environ['GITHUB_TOKEN'],
        api_url=os.environ.get('GITHUB_API_URL', 'https://api.github.com'),
    )
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'digest={oid or ""}\n')
    if oid:
        print(f'reusing verified release commit {oid} from {parsed.tag_ref}')


if __name__ == '__main__':
    main()
