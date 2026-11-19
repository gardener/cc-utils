# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helper used by the `signed-commit` composite-action.

Computes the set of file-changes between the current worktree and `--expected-head-oid`
(optionally restricted to `--paths`), and creates a GitHub-signed commit onto `--branch` via the
`createCommitOnBranch` GraphQL-mutation. Supports a dry-run mode that only prints what would be
committed.

The GraphQL-logic is kept self-contained in this script (rather than importing `github.commit`)
on purpose: this file is always loaded from the pinned action-ref via `GITHUB_ACTION_PATH`,
whereas `github.commit` would have to be present in the installed `gardener-gha-libs` bundle -
which, on GHE-runners, is built from github.com master and would not contain a module that only
exists on a feature-branch. Only `github.github_api` (for the authenticated session, incl. host
handling) is taken from the installed package, which is always present.
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
    commit {
      oid
      signature { isValid state wasSignedByGitHub }
    }
  }
}
'''


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
) -> list[FileChange]:
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

    changes: list[FileChange] = []
    it = iter(tokens)
    for status in it:
        path = next(it)
        if status.startswith('D'):
            changes.append(FileChange(path=path, delete=True))
        else:  # A, M, T, ...
            with open(path, 'rb') as f:
                content = f.read()
            changes.append(FileChange(path=path, content=content))

    return changes


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
    creates a GitHub-signed commit on `branch` via the `createCommitOnBranch` mutation and returns
    the resulting (signed) commit-oid. The branch-ref is advanced atomically by the mutation.
    The returned signature is checked afterwards; verification failure does not undo the mutation.

    If `changes` is empty and `allow_empty` is set, an empty commit (same tree as
    `expected_head_oid`) is created - `fileChanges` is omitted from the mutation. This mirrors
    `git commit --allow-empty` and is needed for release-commits, which frequently carry no diff.
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
        # always send fileChanges (even with empty additions/deletions): omitting it entirely
        # makes createCommitOnBranch fail server-side with an opaque "Something went wrong" error.
        # Empty arrays produce an empty commit (same tree as expected_head_oid).
        'fileChanges': {'additions': additions, 'deletions': deletions},
    }

    variables = {'input': commit_input}

    session = github.github_api(token=token).session
    response = session.post(
        _graphql_endpoint(host),
        json={'query': _MUTATION, 'variables': variables},
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get('errors'):
        raise RuntimeError(f'createCommitOnBranch failed: {payload["errors"]}')

    commit = payload['data']['createCommitOnBranch']['commit']
    oid = commit['oid']
    signature = commit.get('signature') or {}
    if (
        signature.get('isValid') is not True
        or signature.get('state') != 'VALID'
        or signature.get('wasSignedByGitHub') is not True
    ):
        raise RuntimeError(
            f'createCommitOnBranch returned commit {oid} without a valid GitHub signature '
            f'(isValid={signature.get("isValid")!r}, state={signature.get("state")!r}, '
            f'wasSignedByGitHub={signature.get("wasSignedByGitHub")!r}). '
            f'The branch {repository}:{branch} may already have advanced; inspect the commit '
            'and the GitHub host signing configuration before retrying.'
        )

    return oid


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
    parser.add_argument(
        '--allow-empty',
        action='store_true',
        help='create an empty commit when there are no changes (like git commit --allow-empty)',
    )
    parser.add_argument('--github-output', default=os.environ.get('GITHUB_OUTPUT'))
    parsed = parser.parse_args()

    paths = [line.strip() for line in parsed.paths.splitlines() if line.strip()] or None

    expected_head_oid = parsed.expected_head_oid or _run_git('rev-parse', 'HEAD').strip()

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
