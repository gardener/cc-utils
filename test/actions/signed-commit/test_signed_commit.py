# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import subprocess

import pytest

_ACTION_SCRIPT = os.path.join(
    os.path.dirname(__file__),
    os.pardir, os.pardir, os.pardir,
    '.github', 'actions', 'signed-commit', 'signed_commit.py',
)


def _load_module():
    spec = importlib.util.spec_from_file_location('signed_commit', _ACTION_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = _load_module()


def _git(repo, *args):
    subprocess.run(('git', '-C', repo, *args), check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path)
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.email', 't@example.org')
    _git(path, 'config', 'user.name', 'tester')
    _git(path, 'config', 'commit.gpgsign', 'false')
    (tmp_path / 'VERSION').write_text('1.0.0\n')
    os.makedirs(tmp_path / '.ocm' / 'release-notes')
    (tmp_path / '.ocm' / 'release-notes' / 'old.yaml').write_text('note\n')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-qm', 'init')
    cwd = os.getcwd()
    os.chdir(path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


def _head(repo_path):
    return subprocess.run(
        ('git', '-C', str(repo_path), 'rev-parse', 'HEAD'),
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_bump_scenario_detects_version_and_deletion(repo):
    (repo / 'VERSION').write_text('1.1.0-dev\n')             # version bump
    (repo / '.ocm' / 'release-notes' / 'old.yaml').unlink()  # consumed release-note

    changes = sc.changes_from_worktree(parent_sha=_head(repo))
    by_path = {c.path: c for c in changes}

    assert set(by_path) == {'VERSION', '.ocm/release-notes/old.yaml'}
    assert by_path['VERSION'].content == b'1.1.0-dev\n'
    assert by_path['VERSION'].delete is False
    assert by_path['.ocm/release-notes/old.yaml'].delete is True
    assert by_path['.ocm/release-notes/old.yaml'].content is None


def test_detects_untracked_file(repo):
    (repo / 'NEW.txt').write_text('new\n')
    changes = sc.changes_from_worktree(parent_sha=_head(repo))
    assert any(c.path == 'NEW.txt' and c.content == b'new\n' for c in changes)


def test_binary_content_preserved(repo):
    (repo / 'b.bin').write_bytes(b'\xff\xfe\x00\x01')
    changes = sc.changes_from_worktree(parent_sha=_head(repo))
    binary = next(c for c in changes if c.path == 'b.bin')
    assert binary.content == b'\xff\xfe\x00\x01'


def test_paths_restriction(repo):
    (repo / 'VERSION').write_text('2.0.0\n')
    (repo / 'OTHER.txt').write_text('x\n')
    changes = sc.changes_from_worktree(parent_sha=_head(repo), paths=['VERSION'])
    assert [c.path for c in changes] == ['VERSION']


def test_absent_pathspec_is_tolerated(repo):
    (repo / 'VERSION').write_text('2.0.0\n')
    changes = sc.changes_from_worktree(
        parent_sha=_head(repo),
        paths=['VERSION', '.ocm/does-not-exist/'],
    )
    assert [c.path for c in changes] == ['VERSION']


def test_no_changes_returns_empty(repo):
    assert sc.changes_from_worktree(parent_sha=_head(repo)) == []


# --- git-blob reading: unsupported changes are rejected (not silently altered) ---

def test_symlink_change_rejected(repo):
    # a changed symlink must be rejected rather than following the link and uploading target bytes
    os.symlink('/etc/passwd', repo / 'evil-link')
    _git(str(repo), 'add', '-A')
    with pytest.raises(sc.UnsupportedChange, match='120000'):
        sc.changes_from_worktree(parent_sha=_head(repo))


def test_exec_bit_change_rejected(repo):
    # additions carry no mode field, so an exec-bit flip would produce a differing server tree
    os.chmod(repo / 'VERSION', 0o755)
    _git(str(repo), 'add', '-A')
    with pytest.raises(sc.UnsupportedChange, match='mode change'):
        sc.changes_from_worktree(parent_sha=_head(repo))


def test_gitlink_rejected(repo):
    # a submodule/gitlink (mode 160000) cannot be represented. Commit it, then diff via Mode B
    # (changes_from_worktree's `git add -A` would drop a phantom gitlink without a worktree).
    parent = _head(repo)
    _git(str(repo), 'update-index', '--add', '--cacheinfo',
         '160000', parent, 'submodule')
    _git(str(repo), 'commit', '-qm', 'add gitlink')
    with pytest.raises(sc.UnsupportedChange, match='160000'):
        sc.changes_from_commit(parent_sha=parent, source_commit=_head(repo))


def test_symlink_content_not_followed(repo):
    # sanity: the staged blob for a symlink is its target-string, never the followed file
    (repo / 'target.txt').write_text('a' * 5000 + '\n')
    _git(str(repo), 'add', '-A')
    _git(str(repo), 'commit', '-qm', 'add target')
    os.symlink('target.txt', repo / 'link')
    _git(str(repo), 'add', '-A')
    # prove _blob_bytes reads the link blob (the target string), not the 5001-byte target file
    staged = subprocess.run(
        ('git', '-C', str(repo), 'ls-files', '--stage', 'link'),
        check=True, capture_output=True, text=True,
    ).stdout.split()
    assert staged[0] == '120000'
    assert sc._blob_bytes(staged[1]) == b'target.txt'  # the link target string, ~10 bytes


# --- Mode B (source-commit): reproduce a commit's exact tree, ignore untracked junk ---

def test_source_commit_ignores_untracked(repo):
    # reproduces the P2-a scenario: capture-commit leaves a stray `commit-digest` file; Mode B
    # (diffing the local commit) must NOT include it
    parent = _head(repo)
    (repo / 'VERSION').write_text('2.0.0\n')
    _git(str(repo), 'add', '-A')
    _git(str(repo), 'commit', '-qm', 'bump')
    bump = _head(repo)
    (repo / 'commit-digest').write_text('deadbeefdeadbeef\n')  # stray untracked file

    changes = sc.changes_from_commit(parent_sha=parent, source_commit=bump)
    assert [c.path for c in changes] == ['VERSION']
    assert changes[0].content == b'2.0.0\n'


def test_source_commit_captures_deletion(repo):
    parent = _head(repo)
    (repo / '.ocm' / 'release-notes' / 'old.yaml').unlink()
    _git(str(repo), 'add', '-A')
    _git(str(repo), 'commit', '-qm', 'purge notes')
    changes = sc.changes_from_commit(parent_sha=parent, source_commit=_head(repo))
    assert [(c.path, c.delete) for c in changes] == [('.ocm/release-notes/old.yaml', True)]


# --- create_signed_commit: GraphQL payload (mocked POST) ---

import base64
import unittest.mock


def _mock_session(response_json):
    captured = {}
    response = unittest.mock.Mock()
    response.json.return_value = response_json

    def post(url, json=None, **kwargs):
        captured['url'] = url
        captured['json'] = json
        return response

    session = unittest.mock.Mock()
    session.post.side_effect = post
    return session, captured


def _patch_api(session):
    api = unittest.mock.Mock()
    api.session = session
    return unittest.mock.patch.object(sc.github, 'github_api', return_value=api)


def test_payload_additions_and_deletions():
    ok = {'data': {'createCommitOnBranch': {'commit': {'oid': 'deadbeef'}}}}
    session, captured = _mock_session(ok)
    with _patch_api(session):
        oid = sc.create_signed_commit(
            token='t', repository='gardener/cc-utils', branch='master',
            expected_head_oid='cafe1234', headline='next version: 1.2.3-dev',
            changes=[
                sc.FileChange(path='VERSION', content=b'1.2.3-dev\n'),
                sc.FileChange(path='.ocm/release-notes/old.yaml', delete=True),
            ],
        )
    assert oid == 'deadbeef'
    inp = captured['json']['variables']['input']
    assert inp['branch'] == {
        'repositoryNameWithOwner': 'gardener/cc-utils', 'branchName': 'master',
    }
    assert inp['expectedHeadOid'] == 'cafe1234'
    assert inp['message'] == {'headline': 'next version: 1.2.3-dev', 'body': ''}
    assert inp['fileChanges']['additions'] == [
        {'path': 'VERSION', 'contents': base64.b64encode(b'1.2.3-dev\n').decode('ascii')},
    ]
    assert inp['fileChanges']['deletions'] == [{'path': '.ocm/release-notes/old.yaml'}]


def test_payload_binary_base64():
    ok = {'data': {'createCommitOnBranch': {'commit': {'oid': 'x'}}}}
    session, captured = _mock_session(ok)
    binary = b'\xff\xfe\x00\x01'
    with _patch_api(session):
        sc.create_signed_commit(
            token='t', repository='o/r', branch='b', expected_head_oid='p', headline='m',
            changes=[sc.FileChange(path='bin', content=binary)],
        )
    contents = captured['json']['variables']['input']['fileChanges']['additions'][0]['contents']
    assert base64.b64decode(contents) == binary


def test_empty_changes_rejected_without_allow_empty():
    session, _ = _mock_session({})
    with _patch_api(session):
        with pytest.raises(ValueError):
            sc.create_signed_commit(
                token='t', repository='o/r', branch='b', expected_head_oid='p', headline='m',
                changes=[],
            )
    session.post.assert_not_called()


def test_allow_empty_sends_empty_file_changes():
    ok = {'data': {'createCommitOnBranch': {'commit': {'oid': 'empty-oid'}}}}
    session, captured = _mock_session(ok)
    with _patch_api(session):
        oid = sc.create_signed_commit(
            token='t', repository='o/r', branch='b', expected_head_oid='p',
            headline='release 1.2.3', changes=[], allow_empty=True,
        )
    assert oid == 'empty-oid'
    # fileChanges MUST be present with empty arrays - omitting it makes GitHub fail server-side
    assert captured['json']['variables']['input']['fileChanges'] == {
        'additions': [], 'deletions': [],
    }


def test_graphql_errors_raise():
    err = {'errors': [{'message': 'Expected branch to point to "abc"'}]}
    session, _ = _mock_session(err)
    with _patch_api(session):
        with pytest.raises(RuntimeError, match='createCommitOnBranch failed'):
            sc.create_signed_commit(
                token='t', repository='o/r', branch='b', expected_head_oid='p', headline='m',
                changes=[sc.FileChange(path='a', content=b'x')],
            )


def test_endpoint_defaults_and_ghes():
    assert sc._graphql_endpoint(None) == 'https://api.github.com/graphql'
    assert sc._graphql_endpoint('github.com') == 'https://api.github.com/graphql'
    assert sc._graphql_endpoint('github.tools.sap') == 'https://github.tools.sap/api/graphql'
