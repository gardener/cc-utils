# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import subprocess

import pytest

_SIGN_UPGRADE_SCRIPT = os.path.join(
    os.path.dirname(__file__),
    os.pardir, os.pardir, os.pardir,
    '.github', 'actions', 'ocm-upgrade', 'sign_upgrade_commit.py',
)


def _load_module():
    spec = importlib.util.spec_from_file_location('sign_upgrade_commit', _SIGN_UPGRADE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sign_mod = _load_module()


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


def test_sign_commit_onto_branch_stages_and_signs(repo, monkeypatch):
    (repo / 'VERSION').write_text('2.0.0\n')
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return 'signedsha'

    monkeypatch.setattr(sign_mod.signed_commit, 'create_signed_commit', fake_create)
    oid = sign_mod.sign_commit_onto_branch(
        repo_dir=str(repo), token='t', repository_full_name='o/r',
        branch='ci-abc', expected_head_oid=_head(repo), message='Upgrade x',
    )
    assert oid == 'signedsha'
    assert captured['branch'] == 'ci-abc'
    assert captured['repository'] == 'o/r'
    assert captured['expected_head_oid'] == _head(repo)
    assert captured['headline'] == 'Upgrade x'
    assert any(c.path == 'VERSION' for c in captured['changes'])


def test_sign_commit_splits_headline_and_body(repo, monkeypatch):
    (repo / 'VERSION').write_text('2.0.0\n')
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return 'signedsha'

    monkeypatch.setattr(sign_mod.signed_commit, 'create_signed_commit', fake_create)
    sign_mod.sign_commit_onto_branch(
        repo_dir=str(repo), token='t', repository_full_name='o/r',
        branch='ci-abc', expected_head_oid=_head(repo),
        message='Upgrade acme.org/c\n\nfrom 1.0.0 to 2.0.0',
    )
    assert captured['headline'] == 'Upgrade acme.org/c'
    assert captured['body'] == 'from 1.0.0 to 2.0.0'


def test_sign_commit_rejects_empty_token(repo):
    with pytest.raises(ValueError):
        sign_mod.sign_commit_onto_branch(
            repo_dir=str(repo), token='', repository_full_name='o/r',
            branch='ci-abc', expected_head_oid=_head(repo), message='m')


def test_sign_commit_rejects_no_changes(repo):
    with pytest.raises(ValueError):
        sign_mod.sign_commit_onto_branch(
            repo_dir=str(repo), token='t', repository_full_name='o/r',
            branch='ci-abc', expected_head_oid=_head(repo), message='m')
