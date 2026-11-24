# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helper used by the `signed-commit` composite-action.

Computes exact Git-tree changes from an immutable `--source-commit`, or from the staged worktree
(optionally restricted to `--paths`), against `--expected-head-oid`. Creates a GitHub-signed commit
onto `--branch` via `createCommitOnBranch` and checks its tree. Supports a dry-run mode.

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
import tempfile

import github


_MUTATION = '''
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit {
      oid
      tree { oid }
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


@dataclasses.dataclass
class _TreeChange:
    path: str
    old_mode: str
    new_mode: str
    new_oid: str


def _run_git_bytes(*args: str, input: bytes | None=None, env=None) -> bytes:
    return subprocess.run(
        ('git', *args),
        check=True,
        capture_output=True,
        input=input,
        env=env,
    ).stdout


def _run_git(*args: str) -> str:
    return _run_git_bytes(*args).decode('utf-8')


def _commit_oid(revision: str) -> str:
    return _run_git('rev-parse', '--verify', '--end-of-options', f'{revision}^{{commit}}').strip()


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


def _tree_changes(
    parent_sha: str,
    tree_oid: str,
    paths: list[str] | None=None,
) -> list[_TreeChange]:
    raw = _run_git_bytes(
        'diff', '--raw', '--no-abbrev', '--no-renames', '--no-ext-diff',
        '--ignore-submodules=none', '-z', parent_sha, tree_oid, '--', *(paths or []),
    )
    # Raw -z output: :<old-mode> <new-mode> <old-oid> <new-oid> <status>\0<path>\0
    tokens = iter(raw.split(b'\0')[:-1])
    changes = []
    for metadata in tokens:
        old_mode, new_mode, _, new_oid, _ = metadata.decode('ascii').removeprefix(':').split()
        path = next(tokens).decode('utf-8')
        # FileAddition has no mode. Existing regular files retain theirs, new files are 100644.
        # Do not turn symlinks/gitlinks into regular files or try to manufacture executable bits.
        if new_mode == '000000':
            supported = old_mode in ('100644', '100755', '120000')
        elif old_mode == '000000':
            supported = new_mode == '100644'
        else:
            supported = old_mode == new_mode and new_mode in ('100644', '100755')
        if not supported:
            raise ValueError(
                f'unsupported Git mode transition for {path!r}: {old_mode} -> {new_mode}; '
                'createCommitOnBranch cannot set file modes'
            )
        changes.append(_TreeChange(path, old_mode, new_mode, new_oid))
    return changes


def _restricted_tree(parent_sha: str, changes: list[_TreeChange]) -> str:
    '''Construct the selected tree without disturbing unrelated entries in the real index.'''
    with tempfile.TemporaryDirectory(prefix='signed-commit-') as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, 'index'))
        _run_git_bytes('read-tree', parent_sha, env=env)
        # Remove old paths first so file/directory replacements are valid in either direction.
        index_info = b''.join(
            f'{change.new_mode} {change.new_oid}\t{change.path}\0'.encode('utf-8')
            for change in sorted(changes, key=lambda c: c.new_mode != '000000')
        )
        _run_git_bytes('update-index', '-z', '--index-info', input=index_info, env=env)
        return _run_git_bytes('write-tree', env=env).decode('ascii').strip()


def _prepare_changes(
    parent_sha: str,
    paths: list[str] | None=None,
    source_commit: str | None=None,
) -> tuple[list[FileChange], str]:
    parent_sha = _commit_oid(parent_sha)
    if source_commit:
        if paths:
            raise ValueError('source-commit cannot be combined with paths')
        source_oid = _commit_oid(source_commit)
        # Read the actual commit headers: rev-list hides parents at shallow boundaries.
        headers = _run_git_bytes('cat-file', 'commit', source_oid).split(b'\n\n', 1)[0].splitlines()
        parents = [line.removeprefix(b'parent ').decode('ascii')
                   for line in headers if line.startswith(b'parent ')]
        if parents != [parent_sha]:
            raise ValueError(
                f'source-commit {source_oid} must have exactly one parent matching '
                f'expected-head-oid {parent_sha}; got {parents}'
            )
        tree_oid = _run_git('rev-parse', f'{source_oid}^{{tree}}').strip()
    else:
        _stage(paths)
        tree_oid = _run_git('write-tree').strip()

    tree_changes = _tree_changes(parent_sha, tree_oid, paths)
    if paths:
        tree_oid = _restricted_tree(parent_sha, tree_changes)
    changes = [
        FileChange(path=c.path, delete=True) if c.new_mode == '000000' else
        FileChange(path=c.path, content=_run_git_bytes('cat-file', 'blob', c.new_oid))
        for c in tree_changes
    ]
    return changes, tree_oid


def changes_from_worktree(
    parent_sha: str,
    paths: list[str] | None=None,
) -> list[FileChange]:
    '''
    stages worktree-changes (including untracked files) and reads the resulting Git blobs.
    `paths` restricts both staging and the diff. Unrepresentable mode changes are rejected.
    '''
    changes, _ = _prepare_changes(parent_sha=parent_sha, paths=paths)
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
    expected_tree_oid: str | None=None,
    body: str='',
) -> str:
    '''
    creates a GitHub-signed commit on `branch` via the `createCommitOnBranch` mutation and returns
    the resulting (signed) commit-oid. The branch-ref is advanced atomically by the mutation.
    The returned signature is checked afterwards; verification failure does not undo the mutation.

    If `changes` is empty and `allow_empty` is set, an empty commit (same tree as
    `expected_head_oid`) is created. This mirrors `git commit --allow-empty` and is needed for
    release-commits, which frequently carry no diff. If `expected_tree_oid` is given, the returned
    commit's tree must match it. The CLI always supplies the exact intended tree.
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
        'message': {'headline': headline, 'body': body},
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

    if expected_tree_oid is not None:
        actual_tree_oid = commit['tree']['oid']
        if actual_tree_oid != expected_tree_oid:
            raise RuntimeError(
                f'created commit {commit["oid"]} tree mismatch: '
                f'expected {expected_tree_oid}, got {actual_tree_oid}'
            )
    return oid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True, help='<org>/<repo>')
    parser.add_argument('--branch', required=True, help='branch to commit onto (unqualified)')
    parser.add_argument('--message', required=True, help='commit headline')
    parser.add_argument('--token', default=os.environ.get('GITHUB_TOKEN'))
    parser.add_argument(
        '--source-commit',
        default='',
        help='sign this immutable commit tree; its single parent must equal expected-head-oid',
    )
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

    expected_head_oid = _commit_oid(parsed.expected_head_oid or 'HEAD')

    changes, tree_oid = _prepare_changes(
        parent_sha=expected_head_oid, paths=paths, source_commit=parsed.source_commit,
    )
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
        expected_tree_oid=tree_oid,
    )

    print(f'created signed commit {commit_oid} on {parsed.branch}')
    if parsed.github_output:
        with open(parsed.github_output, 'a') as f:
            f.write(f'commit-sha={commit_oid}\n')


if __name__ == '__main__':
    main()
