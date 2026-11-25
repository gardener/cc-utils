# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'pin-actions-and-workflows')
    ),
)

import pin


def _git(path, *args):
    return subprocess.run(
        ('git', '-C', str(path), *args), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _write(repo, rel, text):
    abspath = os.path.join(str(repo), rel)
    os.makedirs(os.path.dirname(abspath), exist_ok=True)
    with open(abspath, 'w') as f:
        f.write(text)


def _init_repo(tmp_path):
    path = str(tmp_path)
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.email', 't@example.org')
    _git(path, 'config', 'user.name', 'tester')
    _git(path, 'config', 'commit.gpgsign', 'false')
    _git(path, 'remote', 'add', 'origin', 'git@github.com:myorg/myrepo.git')
    return path


# leaf action (no internal deps) + a workflow depending on it via @master
_LEAF = '''\
name: leaf
runs:
  using: composite
  steps:
    - run: echo leaf
'''

_PARENT = '''\
name: parent
on: push
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - uses: myorg/myrepo/.github/actions/leaf@master
'''


@pytest.fixture
def tmp_repo(tmp_path):
    path = _init_repo(tmp_path)
    _write(path, '.github/actions/leaf/action.yaml', _LEAF)
    _write(path, '.github/workflows/parent.yaml', _PARENT)
    _git(path, 'add', '-A')
    _git(path, 'commit', '-qm', 'init')
    return path


@pytest.fixture
def tmp_repo_already_pinned(tmp_path):
    # parent already references leaf by a SHA -> nothing to rewrite
    path = _init_repo(tmp_path)
    _write(path, '.github/actions/leaf/action.yaml', _LEAF)
    _git(path, 'add', '-A')
    _git(path, 'commit', '-qm', 'init')
    head = _git(path, 'rev-parse', 'HEAD')
    pinned_parent = _PARENT.replace('@master', f'@{head}')
    _write(path, '.github/workflows/parent.yaml', pinned_parent)
    _git(path, 'add', '-A')
    _git(path, 'commit', '-qm', 'add pinned parent')
    return path


def test_chain_uses_previous_signed_oid_as_parent(tmp_repo, monkeypatch):
    own_digest = _git(tmp_repo, 'rev-parse', 'HEAD')
    calls = []

    def fake_sign(**kw):
        calls.append(kw)
        return f'oid{len(calls)}'

    monkeypatch.setattr(pin.sign_pin_commit, 'sign_file_commit', fake_sign)
    monkeypatch.setattr(pin, '_create_signing_branch', lambda **kw: 'sign-xyz')
    monkeypatch.setattr(pin, '_move_target_branch', lambda **kw: None)
    monkeypatch.setattr(pin, '_delete_signing_branch', lambda **kw: None)

    pin.create_pinned_branch(
        repo_root=tmp_repo, own_ref='HEAD', target_branch='v1',
        own_org='myorg', own_repo='myrepo', token='t', host='github.com',
    )

    assert len(calls) >= 1
    assert calls[0]['expected_head_oid'] == own_digest
    # each subsequent commit builds on the previously returned signed oid (oid1, oid2, ...)
    for i, call in enumerate(calls[1:], start=1):
        assert call['expected_head_oid'] == f'oid{i}'


def test_host_derived_from_env_when_not_given(tmp_repo, monkeypatch):
    # host=None must be resolved from the environment (GHE support), not left as None
    # (which would send createCommitOnBranch to api.github.com).
    calls = []
    monkeypatch.setattr(pin.sign_pin_commit, 'sign_file_commit',
                        lambda **kw: calls.append(kw) or f'oid{len(calls)}')
    monkeypatch.setattr(pin, '_create_signing_branch', lambda **kw: 'sign-xyz')
    monkeypatch.setattr(pin, '_move_target_branch', lambda **kw: None)
    monkeypatch.setattr(pin, '_delete_signing_branch', lambda **kw: None)
    monkeypatch.setenv('GITHUB_SERVER_URL', 'https://github.tools.sap')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'gardener/cc-utils')

    pin.create_pinned_branch(
        repo_root=tmp_repo, own_ref='HEAD', target_branch='v1',
        own_org='myorg', own_repo='myrepo', token='t',  # host NOT passed
    )
    assert calls, 'expected at least one signed commit'
    assert all(c['host'] == 'github.tools.sap' for c in calls)


def test_signing_branch_cleaned_up_on_commit_failure(tmp_repo, monkeypatch):
    # if sign_file_commit raises mid-chain, the already-created signing branch must be deleted.
    deleted = []
    monkeypatch.setattr(pin, '_create_signing_branch', lambda **kw: 'sign-leak')
    monkeypatch.setattr(pin, '_delete_signing_branch',
                        lambda *, repo, branch: deleted.append(branch))
    monkeypatch.setattr(pin, '_move_target_branch', lambda **kw: None)

    def boom(**kw):
        raise RuntimeError('createCommitOnBranch failed mid-chain')

    monkeypatch.setattr(pin.sign_pin_commit, 'sign_file_commit', boom)

    with pytest.raises(RuntimeError):
        pin.create_pinned_branch(
            repo_root=tmp_repo, own_ref='HEAD', target_branch='v1',
            own_org='myorg', own_repo='myrepo', token='t', host='github.com',
        )
    assert deleted == ['sign-leak']


def test_dry_run_makes_no_remote_calls(tmp_repo, monkeypatch):
    called = []
    monkeypatch.setattr(pin.sign_pin_commit, 'sign_file_commit',
                        lambda **kw: called.append('sign') or 'x')
    monkeypatch.setattr(pin, '_create_signing_branch',
                        lambda **kw: called.append('branch') or 'b')
    monkeypatch.setattr(pin, '_move_target_branch',
                        lambda **kw: called.append('move') or None)

    pin.create_pinned_branch(
        repo_root=tmp_repo, own_ref='HEAD', target_branch='v1',
        own_org='myorg', own_repo='myrepo', token='t', dry_run=True,
    )
    assert called == []


def test_empty_run_no_signing_branch(tmp_repo_already_pinned, monkeypatch):
    called = []
    monkeypatch.setattr(pin, '_create_signing_branch',
                        lambda **kw: called.append('branch') or 'b')
    monkeypatch.setattr(pin, '_move_target_branch',
                        lambda **kw: called.append('move') or None)
    monkeypatch.setattr(pin.sign_pin_commit, 'sign_file_commit',
                        lambda **kw: called.append('sign') or 'x')

    pin.create_pinned_branch(
        repo_root=tmp_repo_already_pinned, own_ref='HEAD', target_branch='v1',
        own_org='myorg', own_repo='myrepo', token='t',
    )
    assert called == []  # nothing to rewrite -> no branch, no signing, no move


# --- _move_target_branch: refs are moved via REST (not `git push <tip>`), because the chain is
#     authored server-side and `tip` is not a local object ---

class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class _FakeSession:
    def __init__(self, post_status=201):
        self.post_status = post_status
        self.calls = []

    def patch(self, url, json):
        self.calls.append(('PATCH', url, json))
        return _FakeResponse(200)

    def post(self, url, json):
        self.calls.append(('POST', url, json))
        return _FakeResponse(self.post_status)


def _patch_session(monkeypatch, session):
    # github.github_api(token=...).session -> our fake
    class _Api:
        def __init__(self, session):
            self.session = session

    monkeypatch.setattr(
        pin.sign_pin_commit.signed_commit.github,
        'github_api',
        lambda token: _Api(session),
    )


def test_move_target_branch_uses_rest_refs(monkeypatch):
    session = _FakeSession()
    _patch_session(monkeypatch, session)

    pin._move_target_branch(
        token='t', repository_full_name='myorg/myrepo', target_branch='v1',
        tip='deadbeef', own_digest='cafef00d', host='github.com',
    )

    patch, post = session.calls
    assert patch == (
        'PATCH',
        'https://api.github.com/repos/myorg/myrepo/git/refs/heads/v1',
        {'sha': 'deadbeef', 'force': True},
    )
    assert post == (
        'POST',
        'https://api.github.com/repos/myorg/myrepo/git/refs',
        {'ref': 'refs/tags/fixated/cafef00d', 'sha': 'deadbeef'},
    )


def test_move_target_branch_uses_ghe_api_base(monkeypatch):
    session = _FakeSession()
    _patch_session(monkeypatch, session)

    pin._move_target_branch(
        token='t', repository_full_name='gardener/cc-utils', target_branch='v1',
        tip='deadbeef', own_digest='cafef00d', host='github.tools.sap',
    )

    assert all(call[1].startswith('https://github.tools.sap/api/v3/') for call in session.calls)


def test_move_target_branch_tolerates_existing_preservation_ref(monkeypatch):
    # a re-run with the same own-digest re-creates the preservation ref -> POST returns 422; this
    # must not fail the pin-run (the branch move already succeeded).
    session = _FakeSession(post_status=422)
    _patch_session(monkeypatch, session)

    pin._move_target_branch(  # must not raise
        token='t', repository_full_name='myorg/myrepo', target_branch='v1',
        tip='deadbeef', own_digest='cafef00d', host='github.com',
    )
    assert [c[0] for c in session.calls] == ['PATCH', 'POST']
