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
