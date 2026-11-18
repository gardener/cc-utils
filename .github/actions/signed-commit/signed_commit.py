# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helper used by the `signed-commit` composite-action.

Computes the set of file-changes between the current worktree and `--expected-head-oid`
(optionally restricted to `--paths`), and creates a GitHub-signed commit onto `--branch` via the
`createCommitOnBranch` GraphQL-mutation (see `github.commit`). Supports a dry-run mode that only
prints what would be committed.
'''

import argparse
import os
import subprocess

import github
import github.commit


def _run_git(*args: str) -> str:
    return subprocess.run(
        ('git', *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _stage(paths: list[str] | None) -> None:
    '''
    stages all worktree-changes (incl. untracked files and deletions) via `git add -A`.

    If `paths` is given, each pathspec is staged individually and ones that match nothing
    (e.g. an optional directory that does not exist) are tolerated.
    '''
    if not paths:
        _run_git('add', '-A')
        return
    for path in paths:
        try:
            _run_git('add', '-A', '--', path)
        except subprocess.CalledProcessError:
            pass  # pathspec matched nothing - skip it


def changes_from_worktree(
    parent_sha: str,
    paths: list[str] | None=None,
) -> list[github.commit.FileChange]:
    '''
    determines added/modified/deleted files between `parent_sha` and the current worktree.

    All worktree-changes (including untracked files) are considered: the index is first updated
    via `git add -A` (restricted to `paths` if given), then the staged tree is diffed against
    `parent_sha`.
    '''
    _stage(paths)

    diff_args = ['diff', '--no-renames', '--name-status', '-z', '--cached', parent_sha, '--']
    if paths:
        diff_args += paths

    raw = _run_git(*diff_args)
    # -z output: <status>\0<path>\0<status>\0<path>... (renames excluded via --no-renames)
    tokens = [token for token in raw.split('\0') if token]

    changes: list[github.commit.FileChange] = []
    it = iter(tokens)
    for status in it:
        path = next(it)
        if status.startswith('D'):
            changes.append(github.commit.FileChange(path=path, delete=True))
        else:  # A, M, T, ...
            with open(path, 'rb') as f:
                content = f.read()
            changes.append(github.commit.FileChange(path=path, content=content))

    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True, help='<org>/<repo>')
    parser.add_argument('--branch', required=True, help='branch to commit onto (unqualified)')
    parser.add_argument('--message', required=True, help='commit headline')
    parser.add_argument('--token', default=os.environ.get('GITHUB_TOKEN'))
    parser.add_argument(
        '--expected-head-oid',
        default=None,
        help='oid the branch is expected at (defaults to current HEAD)',
    )
    parser.add_argument(
        '--paths',
        default='',
        help='newline-separated pathspecs to restrict the commit to',
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--github-output', default=os.environ.get('GITHUB_OUTPUT'))
    parsed = parser.parse_args()

    paths = [line.strip() for line in parsed.paths.splitlines() if line.strip()] or None

    expected_head_oid = parsed.expected_head_oid or _run_git('rev-parse', 'HEAD').strip()

    changes = changes_from_worktree(parent_sha=expected_head_oid, paths=paths)
    if not changes:
        print('no changes to commit - nothing to do')
        return

    if parsed.dry_run:
        print(f'[dry-run] branch={parsed.branch} expected-head-oid={expected_head_oid}')
        print(f'[dry-run] message={parsed.message!r}')
        for change in changes:
            kind = 'delete' if change.delete else 'write'
            size = 0 if change.delete else len(change.content)
            print(f'[dry-run]   {kind}: {change.path} ({size} octets)')
        return

    host, _, _ = github.host_org_and_repo(repo_url=None)
    commit_oid = github.commit.create_signed_commit(
        token=parsed.token,
        repository=parsed.repository,
        branch=parsed.branch,
        expected_head_oid=expected_head_oid,
        headline=parsed.message,
        changes=changes,
        host=host,
    )

    print(f'created signed commit {commit_oid} on {parsed.branch}')
    if parsed.github_output:
        with open(parsed.github_output, 'a') as f:
            f.write(f'commit-sha={commit_oid}\n')


if __name__ == '__main__':
    main()
