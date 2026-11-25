# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(__file__),
    os.pardir, os.pardir, os.pardir,
    '.github', 'actions', 'pin-actions-and-workflows', 'sign_pin_commit.py',
)


def _load():
    spec = importlib.util.spec_from_file_location('sign_pin_commit', _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


def test_sign_file_commit_passes_single_filechange(monkeypatch):
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return 'signedoid'

    monkeypatch.setattr(mod.signed_commit, 'create_signed_commit', fake_create)
    oid = mod.sign_file_commit(
        token='t', repository_full_name='o/r', branch='sign-x',
        expected_head_oid='parent123', path='.github/workflows/a.yaml',
        content=b'new content\n', message='pin: rewrite a.yaml\n\nown-commit: deadbeef',
    )
    assert oid == 'signedoid'
    assert captured['branch'] == 'sign-x'
    assert captured['expected_head_oid'] == 'parent123'
    assert captured['repository'] == 'o/r'
    assert captured['headline'] == 'pin: rewrite a.yaml'
    assert captured['body'] == 'own-commit: deadbeef'
    assert len(captured['changes']) == 1
    assert captured['changes'][0].path == '.github/workflows/a.yaml'
    assert captured['changes'][0].content == b'new content\n'


def test_sign_file_commit_rejects_empty_token():
    with pytest.raises(ValueError):
        mod.sign_file_commit(
            token='', repository_full_name='o/r', branch='b',
            expected_head_oid='p', path='x', content=b'y', message='m')
