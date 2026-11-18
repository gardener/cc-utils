# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

'''
helpers for creating GitHub-signed commits via the git-data REST-API.

Commits created locally (`git commit`) and pushed are not signed by GitHub, thus do not show
up as "Verified". Commits created *through the GitHub-API* (while authenticated using an
installation-token, e.g. the one minted by the `github-auth` action) are signed by GitHub
automatically, provided no explicit committer is passed.

This module offers a thin wrapper around github3's git-data methods
(`create_blob` / `create_tree` / `create_commit` / refs) to build such signed commits from a
set of file-changes.
'''

import collections.abc
import dataclasses

import github3.repos.repo


@dataclasses.dataclass
class FileChange:
    '''
    a single file-change to include in a commit.

    `content` holds the new file-contents (as bytes); it must be `None` iff `delete` is set.
    `mode` is the git file-mode (`100644` for regular files, `100755` for executables,
    `120000` for symlinks).
    '''
    path: str
    content: bytes | None = None
    mode: str = '100644'
    delete: bool = False

    def __post_init__(self):
        if self.delete and self.content is not None:
            raise ValueError(f'{self.path=}: must not pass content for a deletion')
        if not self.delete and self.content is None:
            raise ValueError(f'{self.path=}: content is required unless delete is set')


def _blob_sha(
    repository: github3.repos.repo.Repository,
    content: bytes,
) -> str:
    '''
    creates a blob from the given bytes and returns its sha. utf-8-decodable content is sent
    verbatim; everything else is sent base64-encoded (so binary files are handled correctly).
    '''
    try:
        text = content.decode('utf-8')
        encoding = 'utf-8'
        payload = text
    except UnicodeDecodeError:
        import base64
        encoding = 'base64'
        payload = base64.b64encode(content).decode('ascii')

    return repository.create_blob(content=payload, encoding=encoding)


def create_signed_commit(
    repository: github3.repos.repo.Repository,
    *,
    parent_sha: str,
    message: str,
    files: collections.abc.Iterable[FileChange],
    base_tree_sha: str | None=None,
) -> str:
    '''
    creates a commit via the GitHub git-data-API and returns its sha. The commit is NOT attached
    to any ref (see `update_branch_ref` for that).

    As no committer is passed to `create_commit`, GitHub will sign the resulting commit (when
    authenticated using an installation-/app-token), making it show up as "Verified".

    @param parent_sha: the commit the new commit should have as (single) parent
    @param base_tree_sha: tree to base the new tree on; defaults to the parent-commit's tree
    '''
    files = list(files)
    if not files:
        raise ValueError('refusing to create a commit without any file-changes')

    if base_tree_sha is None:
        base_tree_sha = repository.commit(parent_sha).commit.tree.sha

    tree_entries = []
    for file_change in files:
        if file_change.delete:
            # git-data expresses a deletion as a tree-entry with `sha: None` against a base-tree
            sha = None
        else:
            sha = _blob_sha(repository=repository, content=file_change.content)

        tree_entries.append({
            'path': file_change.path,
            'mode': file_change.mode,
            'type': 'blob',
            'sha': sha,
        })

    tree = repository.create_tree(tree=tree_entries, base_tree=base_tree_sha)
    if not tree:
        # fallback: some github3 versions drop entries whose `sha` is None, so deletions are
        # silently ignored (or the call fails). Rebuild a full tree that omits the deleted
        # paths and does not rely on a base-tree.
        deleted_paths = {f.path for f in files if f.delete}
        base_tree = repository.tree(base_tree_sha, recursive=True)
        full_entries = [
            {
                'path': entry.path,
                'mode': entry.mode,
                'type': entry.type,
                'sha': entry.sha,
            }
            for entry in base_tree.tree
            if entry.type == 'blob' and entry.path not in deleted_paths
        ]
        for file_change in files:
            if file_change.delete:
                continue
            full_entries.append({
                'path': file_change.path,
                'mode': file_change.mode,
                'type': 'blob',
                'sha': _blob_sha(repository=repository, content=file_change.content),
            })
        tree = repository.create_tree(tree=full_entries)
        if not tree:
            raise RuntimeError('failed to create git-tree via github-api')

    # note: intentionally no author/committer -> GitHub signs the commit
    commit = repository.create_commit(
        message=message,
        tree=tree.sha,
        parents=[parent_sha],
    )
    if not commit:
        raise RuntimeError('failed to create commit via github-api')

    return commit.sha


def update_branch_ref(
    repository: github3.repos.repo.Repository,
    branch: str,
    sha: str,
    force: bool=False,
) -> None:
    '''
    points `refs/heads/<branch>` at the given commit-sha, creating the branch if it does not
    exist yet. `force` allows non-fast-forward updates (required e.g. for feature-branches that
    are reset to a fresh commit).
    '''
    ref_path = f'heads/{branch}'
    ref = repository.ref(ref_path)

    if ref is None:
        repository.create_ref(f'refs/{ref_path}', sha)
        return

    if not ref.update(sha, force=force):
        raise RuntimeError(f'failed to update ref refs/{ref_path} to {sha} ({force=})')
