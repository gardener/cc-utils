# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import unittest.mock

import pytest

import github.commit as gc


class FakeTree:
    def __init__(self, sha):
        self.sha = sha


class FakeCommitCommit:
    def __init__(self, tree_sha):
        self.tree = FakeTree(tree_sha)


class FakeCommit:
    def __init__(self, tree_sha):
        self.commit = FakeCommitCommit(tree_sha)


class FakeRepository:
    '''records git-data-API calls so tests can assert on them.'''
    def __init__(self, parent_tree_sha='base-tree-sha'):
        self._parent_tree_sha = parent_tree_sha
        self.created_blobs = []       # list of (content, encoding)
        self.created_trees = []       # list of dict(tree=..., base_tree=...)
        self.created_commits = []     # list of kwargs
        self._blob_counter = 0

    def commit(self, sha):
        return FakeCommit(self._parent_tree_sha)

    def create_blob(self, content, encoding):
        self.created_blobs.append((content, encoding))
        self._blob_counter += 1
        return f'blob-sha-{self._blob_counter}'

    def create_tree(self, tree, base_tree=None):
        self.created_trees.append({'tree': tree, 'base_tree': base_tree})
        return FakeTree('new-tree-sha')

    def create_commit(self, message, tree, parents, author=None, committer=None):
        self.created_commits.append({
            'message': message,
            'tree': tree,
            'parents': parents,
            'author': author,
            'committer': committer,
        })
        return unittest.mock.Mock(sha='new-commit-sha')


def test_filechange_validation():
    with pytest.raises(ValueError):
        gc.FileChange(path='x', delete=True, content=b'nope')
    with pytest.raises(ValueError):
        gc.FileChange(path='x')  # neither delete nor content


def test_create_signed_commit_text_file():
    repo = FakeRepository()
    sha = gc.create_signed_commit(
        repository=repo,
        parent_sha='parent-sha',
        message='msg',
        files=[gc.FileChange(path='a.txt', content=b'hello')],
    )

    assert sha == 'new-commit-sha'
    # utf-8-decodable content is sent verbatim with utf-8 encoding
    assert repo.created_blobs == [('hello', 'utf-8')]
    # tree built against the parent-commit's tree
    assert repo.created_trees[0]['base_tree'] == 'base-tree-sha'
    assert repo.created_trees[0]['tree'] == [
        {'path': 'a.txt', 'mode': '100644', 'type': 'blob', 'sha': 'blob-sha-1'},
    ]


def test_binary_content_is_base64_encoded():
    repo = FakeRepository()
    binary = b'\xff\xfe\x00\x01'
    gc.create_signed_commit(
        repository=repo,
        parent_sha='p',
        message='m',
        files=[gc.FileChange(path='bin', content=binary)],
    )
    content, encoding = repo.created_blobs[0]
    assert encoding == 'base64'
    import base64
    assert base64.b64decode(content) == binary


def test_deletion_tree_entry_has_null_sha():
    repo = FakeRepository()
    gc.create_signed_commit(
        repository=repo,
        parent_sha='p',
        message='m',
        files=[gc.FileChange(path='gone', delete=True)],
    )
    # deletions are expressed as tree-entries with sha=None against the base-tree
    assert repo.created_trees[0]['tree'] == [
        {'path': 'gone', 'mode': '100644', 'type': 'blob', 'sha': None},
    ]
    # no blob is created for a deletion
    assert repo.created_blobs == []


def test_commit_is_unsigned_committer_so_github_signs():
    '''the commit MUST be created without an explicit committer, else GitHub won't sign it.'''
    repo = FakeRepository()
    gc.create_signed_commit(
        repository=repo,
        parent_sha='parent-sha',
        message='msg',
        files=[gc.FileChange(path='a', content=b'x')],
    )
    created = repo.created_commits[0]
    assert created['author'] is None
    assert created['committer'] is None
    assert created['parents'] == ['parent-sha']
    assert created['tree'] == 'new-tree-sha'


def test_explicit_base_tree_sha_skips_parent_lookup():
    repo = FakeRepository()
    repo.commit = unittest.mock.Mock(side_effect=AssertionError('should not be called'))
    gc.create_signed_commit(
        repository=repo,
        parent_sha='p',
        message='m',
        base_tree_sha='explicit-tree',
        files=[gc.FileChange(path='a', content=b'x')],
    )
    assert repo.created_trees[0]['base_tree'] == 'explicit-tree'


def test_empty_files_rejected():
    repo = FakeRepository()
    with pytest.raises(ValueError):
        gc.create_signed_commit(repository=repo, parent_sha='p', message='m', files=[])


def test_update_branch_ref_creates_when_absent():
    repo = unittest.mock.Mock()
    repo.ref.return_value = None
    gc.update_branch_ref(repository=repo, branch='feat', sha='abc', force=False)
    repo.create_ref.assert_called_once_with('refs/heads/feat', 'abc')


def test_update_branch_ref_updates_when_present():
    repo = unittest.mock.Mock()
    ref = unittest.mock.Mock()
    ref.update.return_value = True
    repo.ref.return_value = ref
    gc.update_branch_ref(repository=repo, branch='feat', sha='abc', force=True)
    ref.update.assert_called_once_with('abc', force=True)
    repo.create_ref.assert_not_called()


def test_update_branch_ref_raises_on_failed_update():
    repo = unittest.mock.Mock()
    ref = unittest.mock.Mock()
    ref.update.return_value = False
    repo.ref.return_value = ref
    with pytest.raises(RuntimeError):
        gc.update_branch_ref(repository=repo, branch='feat', sha='abc')
