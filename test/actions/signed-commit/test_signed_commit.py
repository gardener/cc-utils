# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import subprocess
import sys
import tempfile

import pytest
import yaml

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
    return subprocess.run(
        ('git', '-C', str(repo), *args), check=True, capture_output=True, text=True,
    ).stdout.strip()


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


# --- create_signed_commit: GraphQL payload (mocked POST) ---

import base64
import re
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


def _signed_commit_response(oid):
    return {
        'data': {'createCommitOnBranch': {'commit': {
            'oid': oid,
            'signature': {
                'isValid': True,
                'state': 'VALID',
                'wasSignedByGitHub': True,
            },
        }}},
    }


def test_payload_additions_and_deletions():
    ok = _signed_commit_response('deadbeef')
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
    ok = _signed_commit_response('x')
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
    ok = _signed_commit_response('empty-oid')
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


@pytest.mark.parametrize('host,endpoint', [
    (None, 'https://api.github.com/graphql'),
    ('github.tools.sap', 'https://github.tools.sap/api/graphql'),
])
def test_valid_github_signature_is_requested_and_accepted(host, endpoint):
    session, captured = _mock_session(_signed_commit_response('signed-oid'))
    with _patch_api(session):
        oid = sc.create_signed_commit(
            token='t', repository='o/r', branch='b', expected_head_oid='p', headline='m',
            changes=[sc.FileChange(path='a', content=b'x')], host=host,
        )

    assert oid == 'signed-oid'
    assert captured['url'] == endpoint
    # Assert only the signature selection so other commit fields may be added independently.
    selection = re.search(r'\bsignature\s*\{([^{}]*)\}', captured['json']['query'])
    assert selection, 'mutation must request commit signature metadata'
    assert {'isValid', 'state', 'wasSignedByGitHub'} <= set(selection[1].split())


@pytest.mark.parametrize('signature_fields', [
    pytest.param({}, id='missing-signature'),
    pytest.param({'signature': None}, id='null-signature'),
    pytest.param({'signature': {}}, id='empty-signature'),
    pytest.param({'signature': {
        'isValid': False, 'state': 'INVALID', 'wasSignedByGitHub': True,
    }}, id='invalid-signature'),
    pytest.param({'signature': {
        'isValid': True, 'state': 'VALID', 'wasSignedByGitHub': False,
    }}, id='non-github-signature'),
    pytest.param({'signature': {
        'isValid': True, 'state': 'UNKNOWN_KEY', 'wasSignedByGitHub': True,
    }}, id='invalid-state'),
    pytest.param({'signature': {
        'state': 'VALID', 'wasSignedByGitHub': True,
    }}, id='missing-is-valid'),
    pytest.param({'signature': {
        'isValid': True, 'wasSignedByGitHub': True,
    }}, id='missing-state'),
    pytest.param({'signature': {
        'isValid': True, 'state': 'VALID',
    }}, id='missing-github-signer'),
    pytest.param({'signature': {
        'isValid': 'false', 'state': 'VALID', 'wasSignedByGitHub': True,
    }}, id='non-boolean-validity'),
    pytest.param({'signature': {
        'isValid': True, 'state': 'VALID', 'wasSignedByGitHub': 'true',
    }}, id='non-boolean-github-signer'),
])
def test_unverified_signature_fails_after_mutation(signature_fields):
    payload = {'data': {'createCommitOnBranch': {
        'commit': {'oid': 'unverified-oid', **signature_fields},
    }}}
    session, _ = _mock_session(payload)
    with _patch_api(session):
        with pytest.raises(RuntimeError, match='signature') as exc:
            sc.create_signed_commit(
                token='t', repository='o/r', branch='release', expected_head_oid='p',
                headline='m', changes=[sc.FileChange(path='a', content=b'x')],
            )

    message = str(exc.value)
    assert 'unverified-oid' in message
    assert 'o/r' in message
    assert 'release' in message
    assert 'already' in message and 'advanced' in message
    assert 'inspect' in message.lower()
    session.post.assert_called_once()  # never attempt a rollback or repeat the mutation


@pytest.mark.parametrize('include_data', [False, True])
def test_graphql_errors_raise(include_data):
    err = _signed_commit_response('signed-oid') if include_data else {}
    err['errors'] = [{'message': 'Expected branch to point to "abc"'}]
    session, _ = _mock_session(err)
    with _patch_api(session):
        with pytest.raises(RuntimeError, match='createCommitOnBranch failed') as exc:
            sc.create_signed_commit(
                token='t', repository='o/r', branch='b', expected_head_oid='p', headline='m',
                changes=[sc.FileChange(path='a', content=b'x')],
            )
    assert 'Expected branch to point to "abc"' in str(exc.value)


def test_endpoint_defaults_and_ghes():
    assert sc._graphql_endpoint(None) == 'https://api.github.com/graphql'
    assert sc._graphql_endpoint('github.com') == 'https://api.github.com/graphql'
    assert sc._graphql_endpoint('github.tools.sap') == 'https://github.tools.sap/api/graphql'


# These tests keep Git real, and replace only the GitHub GraphQL boundary.
def _commit(repo, message='change'):
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', message)
    return _head(repo)


def _main(monkeypatch, parent, *args):
    monkeypatch.delenv('GITHUB_OUTPUT', raising=False)
    monkeypatch.setattr(sys, 'argv', [
        'signed_commit.py', '--repository', 'o/r', '--branch', 'main',
        '--message', 'signed change', '--expected-head-oid', parent, *args,
    ])
    monkeypatch.setattr(sc.github, 'host_org_and_repo', lambda **kwargs: ('github.com', 'o', 'r'))
    sc.main()


def _git_tree_api(repo, reported_tree=None):
    '''Apply the GraphQL file changes to a real, private Git index.'''
    captured = {}
    session = unittest.mock.Mock()

    def post(url, json):
        captured['query'] = json['query']
        inp = captured['input'] = json['variables']['input']
        parent = inp['expectedHeadOid']
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, 'index'))

            def git(*args, input=None):
                return subprocess.run(
                    ('git', '-C', str(repo), *args), env=env, input=input,
                    capture_output=True, check=True,
                ).stdout

            git('read-tree', parent)
            for deletion in inp['fileChanges']['deletions']:
                git('update-index', '--force-remove', '--', deletion['path'])
            for addition in inp['fileChanges']['additions']:
                path = addition['path']
                old_entry = git('ls-tree', parent, '--', path)
                mode = old_entry.split(b' ', 1)[0].decode() if old_entry else '100644'
                blob = git(
                    'hash-object', '-w', '--stdin',
                    input=base64.b64decode(addition['contents']),
                ).strip().decode()
                git('update-index', '--add', '--cacheinfo', mode, blob, path)
            tree = git('write-tree').strip().decode()
            oid = git('commit-tree', tree, '-p', parent, '-m', 'API commit').strip().decode()
        captured.update(tree=tree, oid=oid)
        response = unittest.mock.Mock()
        payload = _signed_commit_response(oid)
        payload['data']['createCommitOnBranch']['commit']['tree'] = {
            'oid': reported_tree or tree,
        }
        response.json.return_value = payload
        return response

    session.post.side_effect = post
    return session, captured


def test_worktree_reads_normalised_index_blobs(repo):
    (repo / '.gitattributes').write_text('VERSION text eol=lf\n')
    (repo / 'VERSION').write_bytes(b'2.0.0\r\n')
    changes = sc.changes_from_worktree(parent_sha=_head(repo))
    assert next(c.content for c in changes if c.path == 'VERSION') == b'2.0.0\n'
    assert (repo / 'VERSION').read_bytes() == b'2.0.0\r\n'


def test_source_commit_signs_exact_tree_and_leaves_workspace_untouched(repo, monkeypatch, tmp_path):
    parent = _head(repo)
    (repo / 'VERSION').write_text('2.0.0\n')
    (repo / 'binary\tname\n.bin').write_bytes(b'\xff\x00\r\n')
    (repo / '.ocm/release-notes/old.yaml').unlink()
    source = _commit(repo)
    expected_tree = _git(repo, 'rev-parse', f'{source}^{{tree}}')
    (repo / 'VERSION').write_text('unrelated staged value\n')
    _git(repo, 'add', 'VERSION')
    (repo / 'VERSION').write_text('unrelated worktree value\n')
    (repo / 'commit-digest').write_text(source)
    index_before = _git(repo, 'write-tree')
    status_before = _git(repo, 'status', '--porcelain=v1', '-z')
    output = tmp_path / '.git' / 'github-output'
    session, captured = _git_tree_api(repo)
    with _patch_api(session):
        _main(monkeypatch, parent, '--source-commit', source, '--github-output', str(output))
    assert captured['tree'] == expected_tree
    assert 'tree' in captured['query']
    assert output.read_text() == f'commit-sha={captured["oid"]}\n'
    assert _head(repo) == source
    assert _git(repo, 'write-tree') == index_before
    assert _git(repo, 'status', '--porcelain=v1', '-z') == status_before
    assert (repo / 'VERSION').read_text() == 'unrelated worktree value\n'


def test_worktree_path_filter_verifies_only_selected_tree(repo, monkeypatch):
    parent = _head(repo)
    (repo / 'unrelated').write_text('already staged\n')
    _git(repo, 'add', 'unrelated')
    (repo / 'VERSION').write_text('2.0.0\n')
    session, captured = _git_tree_api(repo)
    with _patch_api(session):
        _main(monkeypatch, parent, '--paths', 'VERSION')
    assert [a['path'] for a in captured['input']['fileChanges']['additions']] == ['VERSION']
    assert _git(repo, 'ls-tree', captured['tree'], '--', 'unrelated') == ''
    assert _git(repo, 'ls-files', '--', 'unrelated') == 'unrelated'


@pytest.mark.parametrize('old_mode,new_mode', [
    (None, '100755'), ('100644', '100755'), ('100755', '100644'),
    (None, '120000'), ('100644', '120000'), ('120000', '100644'),
    ('120000', '120000'), (None, '160000'), ('160000', '160000'),
])
def test_unrepresentable_modes_fail_before_post(repo, monkeypatch, old_mode, new_mode):
    def entry(mode, value):
        if mode == '160000':
            oid = value
        else:
            oid = subprocess.run(
                ('git', 'hash-object', '-w', '--stdin'), input=value.encode(),
                check=True, capture_output=True,
            ).stdout.decode().strip()
        _git(repo, 'update-index', '--add', '--cacheinfo', mode, oid, 'entry')

    initial = _head(repo)
    if old_mode:
        entry(old_mode, initial if old_mode == '160000' else '/outside/secret')
        _git(repo, 'commit', '-qm', 'old mode')
    parent = _head(repo)
    # The gitlink object is a real commit; the symlink blob is never dereferenced.
    entry(new_mode, parent if new_mode == '160000' else '/different/secret')
    _git(repo, 'commit', '-qm', 'new mode')
    source = _head(repo)
    session, _ = _mock_session({})
    with _patch_api(session):
        with pytest.raises(ValueError, match='mode|gitlink|symlink'):
            _main(monkeypatch, parent, '--source-commit', source)
    session.post.assert_not_called()


def test_existing_executable_content_update_preserves_mode(repo, monkeypatch):
    (repo / 'script').write_text('#!/bin/sh\nexit 0\n')
    (repo / 'script').chmod(0o755)
    parent = _commit(repo)
    (repo / 'script').write_text('#!/bin/sh\nexit 1\n')
    source = _commit(repo)
    session, captured = _git_tree_api(repo)
    with _patch_api(session):
        _main(monkeypatch, parent, '--source-commit', source)
    assert captured['tree'] == _git(repo, 'rev-parse', f'{source}^{{tree}}')
    assert _git(repo, 'ls-tree', captured['tree'], '--', 'script').startswith('100755 blob')


def test_unchanged_special_entries_and_symlink_deletion(repo, monkeypatch):
    (repo / 'link').symlink_to('/outside/secret')
    (repo / 'unchanged-link').symlink_to('/outside/also-secret')
    _git(repo, 'add', '-A')
    _git(repo, 'update-index', '--add', '--cacheinfo', '160000', _head(repo), 'submodule')
    _git(repo, 'commit', '-qm', 'special entries')
    parent = _head(repo)
    (repo / 'link').unlink()
    (repo / 'VERSION').write_text('2.0.0\n')
    _git(repo, 'add', '-u', '--', 'link', 'VERSION')
    _git(repo, 'commit', '-qm', 'delete symlink')
    source = _head(repo)
    session, captured = _git_tree_api(repo)
    with _patch_api(session):
        _main(monkeypatch, parent, '--source-commit', source)
    assert captured['tree'] == _git(repo, 'rev-parse', f'{source}^{{tree}}')
    assert captured['input']['fileChanges']['deletions'] == [{'path': 'link'}]


@pytest.mark.parametrize('case', ['wrong-parent', 'root', 'merge', 'paths'])
def test_source_commit_requires_one_matching_parent_and_full_tree(repo, monkeypatch, case):
    parent = _head(repo)
    source = _git(repo, 'commit-tree', 'HEAD^{tree}', '-p', parent, '-m', 'source')
    if case == 'wrong-parent':
        parent = source
    elif case == 'root':
        source = parent
    elif case == 'merge':
        source = _git(repo, 'commit-tree', 'HEAD^{tree}', '-p', parent, '-p', source, '-m', 'merge')
    extra_args = ['--paths', 'VERSION'] if case == 'paths' else []
    session, _ = _mock_session({})
    with _patch_api(session):
        with pytest.raises(ValueError, match='parent|paths'):
            _main(monkeypatch, parent, '--source-commit', source, *extra_args)
    session.post.assert_not_called()


def test_api_tree_mismatch_fails_without_success_output(repo, monkeypatch, tmp_path):
    parent = _head(repo)
    (repo / 'VERSION').write_text('2.0.0\n')
    source = _commit(repo)
    output = tmp_path / '.git' / 'github-output'
    session, _ = _git_tree_api(repo, reported_tree='0' * 40)
    with _patch_api(session):
        with pytest.raises(RuntimeError, match='tree.*mismatch|tree.*does not match'):
            _main(monkeypatch, parent, '--source-commit', source, '--github-output', str(output))
    assert not output.exists()


def _action(name):
    path = os.path.join(os.path.dirname(_ACTION_SCRIPT), '..', name, 'action.yaml')
    with open(path) as f:
        return yaml.safe_load(f)


def _run_step(repo, step, output, **env):
    subprocess.run(
        ('bash', '-c', step['run']), cwd=repo, check=True, capture_output=True, text=True,
        env=dict(os.environ, GITHUB_OUTPUT=str(output), **env),
    )
    return output.read_text()


def test_release_signing_uses_captured_commit_without_reset(repo, tmp_path):
    parent = _head(repo)
    (repo / 'VERSION').write_text('2.0.0\n')
    source = _commit(repo)
    (repo / 'commit-digest').write_text(source)
    steps = {s.get('id'): s for s in _action('release')['runs']['steps'] if 'id' in s}
    values = _run_step(repo, steps['release-parent'], tmp_path / '.git' / 'release-output')
    assert f'parent={parent}\n' in values
    assert f'source-commit={source}\n' in values
    assert _head(repo) == source
    assert steps['sign-release-commit']['with']['source-commit'] == (
        '${{ steps.release-parent.outputs.source-commit }}'
    )


def test_release_bump_amends_note_deletions_before_capture(repo, tmp_path):
    parent = _head(repo)
    (repo / 'VERSION').write_text('2.0.0-dev\n')
    _commit(repo, 'next version: 2.0.0-dev')
    (repo / '.ocm/release-notes/old.yaml').unlink()
    (repo / '.ocm/release-notes').rmdir()
    (repo / 'commit-digest').write_text('capture helper artifact\n')
    steps = _action('release')['runs']['steps']
    capture = next(s for s in steps if s.get('id') == 'push-bump-commit')
    values = _run_step(
        repo, capture, tmp_path / '.git' / 'bump-output',
        RELEASE_NOTES_PATH='.ocm/release-notes',
    )
    source = _head(repo)
    assert f'branch-tip={parent}\n' in values
    assert f'source-commit={source}\n' in values
    assert _git(repo, 'ls-tree', '-r', '--name-only', source) == 'VERSION'
    assert _git(repo, 'log', '-1', '--format=%s') == 'next version: 2.0.0-dev'
    signing = next(s for s in steps if s.get('name') == 'push-bump-commit-signed')
    assert signing['with']['source-commit'] == (
        '${{ steps.push-bump-commit.outputs.source-commit }}'
    )


@pytest.mark.parametrize('notes_path', ['', '.ocm/nonexistent'])
def test_release_bump_tolerates_absent_notes_path(repo, tmp_path, notes_path):
    (repo / 'VERSION').write_text('2.0.0-dev\n')
    source = _commit(repo)
    capture = next(
        s for s in _action('release')['runs']['steps'] if s.get('id') == 'push-bump-commit'
    )
    values = _run_step(
        repo, capture, tmp_path / '.git' / 'bump-output', RELEASE_NOTES_PATH=notes_path,
    )
    assert f'source-commit={source}\n' in values
    assert _head(repo) == source


def test_release_bump_amend_excludes_unrelated_staged_files(repo, tmp_path):
    (repo / 'VERSION').write_text('2.0.0-dev\n')
    _commit(repo)
    (repo / '.ocm/release-notes/old.yaml').unlink()
    (repo / 'unrelated').write_text('already staged\n')
    _git(repo, 'add', 'unrelated')
    capture = next(
        s for s in _action('release')['runs']['steps'] if s.get('id') == 'push-bump-commit'
    )
    _run_step(
        repo, capture, tmp_path / '.git' / 'bump-output',
        RELEASE_NOTES_PATH='.ocm/release-notes',
    )
    assert _git(repo, 'ls-tree', '-r', '--name-only', 'HEAD') == 'VERSION'
    assert _git(repo, 'diff', '--cached', '--name-only') == 'unrelated'
