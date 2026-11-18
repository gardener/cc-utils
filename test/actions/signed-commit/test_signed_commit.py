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
    (tmp_path / 'tracked.txt').write_text('v1\n')
    (tmp_path / 'to-delete.txt').write_text('bye\n')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-qm', 'init')
    cwd = os.getcwd()
    os.chdir(path)
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)


def _parent(repo_path):
    return subprocess.run(
        ('git', '-C', str(repo_path), 'rev-parse', 'HEAD'),
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_detects_add_modify_delete_including_untracked(repo):
    (repo / 'tracked.txt').write_text('v2\n')       # modify
    (repo / 'to-delete.txt').unlink()               # delete
    (repo / 'added.txt').write_text('new\n')        # add (untracked)

    changes = sc.changes_from_worktree(parent_sha=_parent(repo))
    by_path = {c.path: c for c in changes}

    assert set(by_path) == {'tracked.txt', 'to-delete.txt', 'added.txt'}
    assert by_path['to-delete.txt'].delete is True
    assert by_path['to-delete.txt'].content is None
    assert by_path['tracked.txt'].content == b'v2\n'
    assert by_path['added.txt'].content == b'new\n'


def test_binary_content_preserved_as_bytes(repo):
    (repo / 'b.bin').write_bytes(b'\xff\xfe\x00\x01')
    changes = sc.changes_from_worktree(parent_sha=_parent(repo))
    binary = next(c for c in changes if c.path == 'b.bin')
    assert binary.content == b'\xff\xfe\x00\x01'


def test_paths_restriction(repo):
    (repo / 'tracked.txt').write_text('v2\n')
    (repo / 'added.txt').write_text('new\n')
    changes = sc.changes_from_worktree(parent_sha=_parent(repo), paths=['tracked.txt'])
    assert [c.path for c in changes] == ['tracked.txt']


def test_absent_pathspec_is_tolerated(repo):
    (repo / 'tracked.txt').write_text('v2\n')
    # '.ocm/release-notes/' does not exist - must not raise
    changes = sc.changes_from_worktree(
        parent_sha=_parent(repo),
        paths=['tracked.txt', '.ocm/release-notes/'],
    )
    assert [c.path for c in changes] == ['tracked.txt']


def test_no_changes_returns_empty(repo):
    assert sc.changes_from_worktree(parent_sha=_parent(repo)) == []


def test_executable_mode_detected(repo):
    script = repo / 'run.sh'
    script.write_text('#!/bin/sh\n')
    os.chmod(script, 0o755)
    changes = sc.changes_from_worktree(parent_sha=_parent(repo))
    run = next(c for c in changes if c.path == 'run.sh')
    assert run.mode == '100755'
