# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helper used by the `signed-commit` composite-action.

Computes the set of file-changes between the current worktree and a parent-commit (optionally
restricted to a set of paths), and creates a GitHub-signed commit from them via the git-data-API
(see `github.commit`). Supports a dry-run mode that only prints what would be committed.
'''

import argparse
import os
import subprocess
import sys

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
    (e.g. an optional directory that does not exist) are tolerated - mirroring the previous
    `git add <path> 2>/dev/null || true` behaviour.
    '''
    if not paths:
        _run_git('add', '-A')
        return

    for path in paths:
        try:
            _run_git('add', '-A', '--', path)
        except subprocess.CalledProcessError:
            # pathspec matched nothing (e.g. optional dir absent) - skip it
            pass


def _mode_for(path: str) -> str:
    '''derives the git file-mode for a worktree-path (regular / executable / symlink).'''
    if os.path.islink(path):
        return '120000'
    if os.access(path, os.X_OK):
        return '100755'
    return '100644'


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
        else:  # A, M, T, C, ...
            with open(path, 'rb') as f:
                content = f.read()
            changes.append(github.commit.FileChange(
                path=path,
                content=content,
                mode=_mode_for(path),
            ))

    return changes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True, help='<org>/<repo>')
    parser.add_argument('--branch', required=True)
    parser.add_argument('--message', required=True)
    parser.add_argument('--token', default=os.environ.get('GITHUB_TOKEN'))
    parser.add_argument(
        '--parent-sha',
        default=None,
        help='parent-commit; defaults to the current tip of --branch',
    )
    parser.add_argument('--force', action='store_true')
    parser.add_argument(
        '--paths',
        default='',
        help='newline-separated pathspecs to restrict the commit to',
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--github-output', default=os.environ.get('GITHUB_OUTPUT'))
    parsed = parser.parse_args()

    org, _, repo = parsed.repository.partition('/')
    paths = [line.strip() for line in parsed.paths.splitlines() if line.strip()] or None

    if parsed.dry_run:
        repository = None
    else:
        github_api = github.github_api(token=parsed.token)
        repository = github_api.repository(org, repo)

    if parsed.parent_sha:
        parent_sha = parsed.parent_sha
    elif parsed.dry_run:
        parent_sha = _run_git('rev-parse', 'HEAD').strip()
    else:
        ref = repository.ref(f'heads/{parsed.branch}')
        if ref is None:
            print(f'error: branch {parsed.branch} does not exist and no --parent-sha given')
            sys.exit(1)
        parent_sha = ref.object.sha

    changes = changes_from_worktree(parent_sha=parent_sha, paths=paths)

    if not changes:
        print('no changes to commit - nothing to do')
        return

    if parsed.dry_run:
        print(f'[dry-run] branch={parsed.branch} parent={parent_sha} force={parsed.force}')
        print(f'[dry-run] message={parsed.message!r}')
        for change in changes:
            kind = 'delete' if change.delete else f'write ({change.mode})'
            size = 0 if change.delete else len(change.content)
            print(f'[dry-run]   {kind}: {change.path} ({size} octets)')
        return

    commit_sha = github.commit.create_signed_commit(
        repository=repository,
        parent_sha=parent_sha,
        message=parsed.message,
        files=changes,
    )
    github.commit.update_branch_ref(
        repository=repository,
        branch=parsed.branch,
        sha=commit_sha,
        force=parsed.force,
    )

    print(f'created signed commit {commit_sha} on {parsed.branch}')
    if parsed.github_output:
        with open(parsed.github_output, 'a') as f:
            f.write(f'commit-sha={commit_sha}\n')


if __name__ == '__main__':
    main()
