# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import contextlib
import importlib.util
import os
import subprocess
import sys
import unittest.mock

import pytest

_ACTION_DIR = os.path.join(
    os.path.dirname(__file__),
    os.pardir, os.pardir, os.pardir,
    '.github', 'actions', 'ocm-upgrade',
)
_OCM_UPGRADE_SCRIPT = os.path.join(_ACTION_DIR, 'ocm_upgrade.py')


def _load_module():
    # the action dir is on sys.path at runtime via GITHUB_ACTION_PATH; replicate that for tests so
    # ocm_upgrade's `import sign_upgrade_commit` resolves (keeps that fallback out of prod code).
    sys.path.insert(0, _ACTION_DIR)
    spec = importlib.util.spec_from_file_location('ocm_upgrade', _OCM_UPGRADE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ocm_upgrade = _load_module()


def _git(path, *args):
    return subprocess.run(
        ('git', '-C', str(path), *args), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo_dir(tmp_path):
    path = str(tmp_path)
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.email', 't@example.org')
    _git(path, 'config', 'user.name', 'tester')
    _git(path, 'config', 'commit.gpgsign', 'false')
    (tmp_path / 'VERSION').write_text('1.0.0\n')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-qm', 'init')
    return path


def test_create_upgrade_pullrequest_passes_token_and_signer(repo_dir, monkeypatch):
    seen = {}

    @contextlib.contextmanager
    def fake_cap(*a, github_token, sign, host, **kw):
        seen.update(token=github_token, sign=sign, host=host)
        yield 'ci-x'

    monkeypatch.setattr(
        ocm_upgrade.github.pullrequest, 'commit_and_push_to_tmp_branch', fake_cap)

    # stub the collaborators the body calls before reaching the with-block
    monkeypatch.setattr(ocm_upgrade, 'retrieve_release_notes', lambda **kw: [])
    monkeypatch.setattr(ocm_upgrade.rno, 'group_release_notes_docs', lambda docs: [])
    monkeypatch.setattr(ocm_upgrade.rno, 'release_notes_docs_as_markdown', lambda **kw: '')
    monkeypatch.setattr(ocm_upgrade.github.pullrequest, 'bom_diff', lambda **kw: '')
    monkeypatch.setattr(
        ocm_upgrade.github.pullrequest, 'upgrade_pullrequest_body', lambda **kw: ('', []))
    monkeypatch.setattr(ocm_upgrade, 'create_upgrade_pullrequest_diff', lambda **kw: None)
    monkeypatch.setattr(
        ocm_upgrade.github.pullrequest, 'upgrade_pullrequest_title', lambda **kw: 't')
    monkeypatch.setattr(
        ocm_upgrade.github.pullrequest, 'as_upgrade_pullrequest', lambda **kw: 'upr')

    whence = ocm_upgrade.ocm.ComponentIdentity(name='acme.org/c', version='1.0.0')
    whither = ocm_upgrade.ocm.ComponentIdentity(name='acme.org/c', version='2.0.0')
    upgrade_vector = ocm_upgrade.ocm.gardener.UpgradeVector(whence=whence, whither=whither)

    cd = unittest.mock.Mock()
    cd.component = unittest.mock.Mock()
    cdl = unittest.mock.Mock(return_value=cd)
    repository = unittest.mock.Mock()

    result = ocm_upgrade.create_upgrade_pullrequest(
        upgrade_vector=upgrade_vector,
        component_descriptor_lookup=cdl,
        version_lookup=unittest.mock.Mock(),
        repo_dir=repo_dir,
        repo_url=f'file://{repo_dir}',
        repository=repository,
        merge_policy=ocm_upgrade.MergePolicy.MANUAL,
        merge_method=ocm_upgrade.MergeMethod.MERGE,
        branch='master',
        oci_client=unittest.mock.Mock(),
        github_token='tok',
        host='github.com',
    )

    assert result == 'upr'
    assert seen['token'] == 'tok'
    assert seen['sign'] is ocm_upgrade.sign_upgrade_commit.sign_commit_onto_branch
    assert seen['host'] == 'github.com'
