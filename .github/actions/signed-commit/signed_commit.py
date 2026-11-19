# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helper used by the `signed-commit` composite-action.

Determines a set of file-changes and creates a GitHub-signed commit onto `--branch` via the
`createCommitOnBranch` GraphQL-mutation. Supports a dry-run mode that only prints what would be
committed.

Changes are read from git OBJECTS (not the worktree filesystem): file-contents come from the
staged/committed blob via `git cat-file`, so symlinks yield their (small) target-string blob rather
than the followed file, and clean/LFS/EOL filters cannot make the server-created tree differ from
git's tree. Two sources are supported:

  * worktree  (default)        - `git add -A` + `git diff-index --cached <parent>`
  * `--source-commit <oid>`    - `git diff-tree <parent> <oid>` (reproduces exactly that commit's
                                 tree; does not restage untracked files)

The GraphQL-logic is kept self-contained in this script (rather than importing `github.commit`)
on purpose: this file is always loaded from the pinned action-ref via `GITHUB_ACTION_PATH`,
whereas `github.commit` would have to be present in the installed `gardener-gha-libs` bundle -
which, on GHE-runners, is built from github.com master. Only `github.github_api` (for the
authenticated session, incl. host handling) is taken from the installed package.
'''

import argparse
import base64
import dataclasses
import os
import subprocess

import github


_MUTATION = '''
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit { oid }
  }
}
'''

# git file-modes representable via createCommitOnBranch fileChanges (additions carry no mode field,
# so only regular files work; symlinks (120000) and gitlinks/submodules (160000) cannot be
# faithfully represented and are rejected).
_SUPPORTED_MODES = ('100644', '100755')


class UnsupportedChange(RuntimeError):
    pass


@dataclasses.dataclass
class FileChange:
    path: str
    content: bytes | None = None
    delete: bool = False


def _run_git(*args: str) -> str:
    return subprocess.run(
        ('git', *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _run_git_bytes(*args: str) -> bytes:
    return subprocess.run(
        ('git', *args),
        check=True,
        capture_output=True,
    ).stdout


def _blob_bytes(oid: str) -> bytes:
    '''returns the exact bytes of the git blob (no symlink-follow, no filters).'''
    return _run_git_bytes('cat-file', 'blob', oid)


def _parse_raw_diff(raw: str) -> list[FileChange]:
    '''
    parses NUL-delimited `--raw` diff output (from diff-index/diff-tree) into FileChanges, reading
    content from git blobs. Rejects changes that createCommitOnBranch cannot represent faithfully.

    raw-format per entry: ":<srcmode> <dstmode> <srcoid> <dstoid> <status>\\0<path>\\0"
    '''
    tokens = [token for token in raw.split('\0') if token]
    changes: list[FileChange] = []
    it = iter(tokens)
    for meta in it:
        # meta == ":100644 100755 <srcoid> <dstoid> M"
        srcmode, dstmode, _srcoid, dstoid, status = meta.lstrip(':').split()
        path = next(it)

        if status.startswith('D'):
            changes.append(FileChange(path=path, delete=True))
            continue
        if status.startswith(('R', 'C')):
            # should not occur (we pass --no-renames); guard anyway
            raise UnsupportedChange(f'{path}: rename/copy changes are not supported')

        if dstmode not in _SUPPORTED_MODES:
            raise UnsupportedChange(
                f'{path}: unsupported file-mode {dstmode} '
                '(symlinks and submodules cannot be signed via createCommitOnBranch)'
            )
        if srcmode in _SUPPORTED_MODES and srcmode != dstmode:
            # exec-bit flip: additions have no mode field, so the server tree would differ
            raise UnsupportedChange(
                f'{path}: file-mode change {srcmode}->{dstmode} cannot be represented '
                '(createCommitOnBranch additions carry no mode)'
            )

        changes.append(FileChange(path=path, content=_blob_bytes(dstoid)))

    return changes


def _stage(paths: list[str] | None) -> None:
    '''stages all worktree-changes (incl. untracked + deletions). Paths matching nothing are ok.'''
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
) -> list[FileChange]:
    '''
    Mode A: stage worktree-changes, then diff the index against `parent_sha`. Content is read from
    the staged blobs (not the worktree files).
    '''
    _stage(paths)
    args = ['diff-index', '--cached', '--no-renames', '-z', parent_sha, '--']
    if paths:
        args += paths
    return _parse_raw_diff(_run_git(*args))


def changes_from_commit(parent_sha: str, source_commit: str) -> list[FileChange]:
    '''
    Mode B: diff `source_commit` against `parent_sha`, reproducing exactly that commit's tree.
    Untracked files in the worktree are ignored.
    '''
    return _parse_raw_diff(
        _run_git('diff-tree', '-r', '--no-renames', '-z', parent_sha, source_commit)
    )


def _graphql_endpoint(host: str | None) -> str:
    if not host or host == 'github.com':
        return 'https://api.github.com/graphql'
    return f'https://{host}/api/graphql'


def create_signed_commit(
    *,
    token: str,
    repository: str,
    branch: str,
    expected_head_oid: str,
    headline: str,
    changes: list[FileChange],
    host: str | None=None,
    allow_empty: bool=False,
) -> str:
    '''
    creates a GitHub-signed commit on `branch` via `createCommitOnBranch` and returns its oid. The
    branch-ref is advanced atomically by the mutation.

    If `changes` is empty and `allow_empty` is set, an empty commit (same tree as
    `expected_head_oid`) is created. This mirrors `git commit --allow-empty` and is needed for
    release-commits, which frequently carry no diff.
    '''
    if not changes and not allow_empty:
        raise ValueError('refusing to create a commit without any file-changes')

    additions = [
        {'path': c.path, 'contents': base64.b64encode(c.content).decode('ascii')}
        for c in changes if not c.delete
    ]
    deletions = [{'path': c.path} for c in changes if c.delete]

    commit_input = {
        'branch': {
            'repositoryNameWithOwner': repository,
            'branchName': branch,
        },
        'expectedHeadOid': expected_head_oid,
        'message': {'headline': headline, 'body': ''},
        # always send fileChanges (even empty): omitting it makes the mutation fail server-side with
        # an opaque error. Empty arrays produce an empty commit (same tree as expected_head_oid).
        'fileChanges': {'additions': additions, 'deletions': deletions},
    }

    session = github.github_api(token=token).session
    response = session.post(
        _graphql_endpoint(host),
        json={'query': _MUTATION, 'variables': {'input': commit_input}},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('errors'):
        raise RuntimeError(f'createCommitOnBranch failed: {payload["errors"]}')

    return payload['data']['createCommitOnBranch']['commit']['oid']


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
        '--source-commit',
        default='',
        help='reproduce this commit\'s tree (diff vs parent) instead of staging the worktree',
    )
    parser.add_argument(
        '--paths',
        default='',
        help='newline-separated pathspecs to restrict the (worktree) commit to',
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument(
        '--allow-empty',
        action='store_true',
        help='create an empty commit when there are no changes (like git commit --allow-empty)',
    )
    parser.add_argument('--github-output', default=os.environ.get('GITHUB_OUTPUT'))
    parsed = parser.parse_args()

    paths = [line.strip() for line in parsed.paths.splitlines() if line.strip()] or None
    source_commit = parsed.source_commit.strip() or None

    expected_head_oid = parsed.expected_head_oid or _run_git('rev-parse', 'HEAD').strip()

    if source_commit:
        changes = changes_from_commit(parent_sha=expected_head_oid, source_commit=source_commit)
    else:
        changes = changes_from_worktree(parent_sha=expected_head_oid, paths=paths)

    if not changes and not parsed.allow_empty:
        print('no changes to commit - nothing to do')
        return

    if parsed.dry_run:
        print(f'[dry-run] branch={parsed.branch} expected-head-oid={expected_head_oid}')
        print(f'[dry-run] message={parsed.message!r}')
        if not changes:
            print('[dry-run]   (empty commit - no file-changes)')
        for change in changes:
            kind = 'delete' if change.delete else 'write'
            size = 0 if change.delete else len(change.content)
            print(f'[dry-run]   {kind}: {change.path} ({size} octets)')
        return

    host, _, _ = github.host_org_and_repo(repo_url=None)
    commit_oid = create_signed_commit(
        token=parsed.token,
        repository=parsed.repository,
        branch=parsed.branch,
        expected_head_oid=expected_head_oid,
        headline=parsed.message,
        changes=changes,
        host=host,
        allow_empty=parsed.allow_empty,
    )

    print(f'created signed commit {commit_oid} on {parsed.branch}')
    if parsed.github_output:
        with open(parsed.github_output, 'a') as f:
            f.write(f'commit-sha={commit_oid}\n')


if __name__ == '__main__':
    main()
