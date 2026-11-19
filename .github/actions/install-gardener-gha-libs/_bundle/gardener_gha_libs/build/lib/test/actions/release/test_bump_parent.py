# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import os
import pathlib
import subprocess

import pytest
import yaml


_ACTION = pathlib.Path(__file__).resolve().parents[3] / '.github/actions/release/action.yaml'


def _git(repo, *args):
    return subprocess.run(
        ('git', '-C', str(repo), *map(str, args)),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_step(repo, name, github_ref='refs/heads/main', **env):
    action = yaml.safe_load(_ACTION.read_text())
    step = next(step for step in action['runs']['steps'] if step.get('name') == name)
    script = step['run'].replace('${{ github.ref }}', github_ref)
    return subprocess.run(
        ('bash', '--noprofile', '--norc', '-c', script),
        cwd=repo,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    # Keep these real, local-only repositories independent of the user's Git configuration.
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'tester')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 't@example.org')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'tester')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 't@example.org')
    monkeypatch.setenv('GIT_TERMINAL_PROMPT', '0')

    origin = tmp_path / 'origin.git'
    origin.mkdir()
    _git(origin, 'init', '--bare', '-q')

    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    _git(checkout, 'init', '-q', '-b', 'main')
    _git(checkout, 'config', 'commit.gpgsign', 'false')
    _git(checkout, 'remote', 'add', 'origin', origin)
    (checkout / 'VERSION').write_text('1.0.0-dev\n')
    _git(checkout, 'add', 'VERSION')
    _git(checkout, 'commit', '-qm', 'initial version')
    _git(checkout, 'push', '-u', 'origin', 'main')
    return checkout, origin


def _release_commit(checkout):
    (checkout / 'VERSION').write_text('1.0.0\n')
    (checkout / 'release-only.txt').write_text('release content\n')
    _git(checkout, 'add', '-A')
    _git(checkout, 'commit', '-qm', 'release 1.0.0')
    return _git(checkout, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('release_target', ('tag', 'branch'))
@pytest.mark.parametrize('upstream_update', (False, True))
def test_bump_parent_is_current_source_branch_tip(
    repositories,
    tmp_path,
    release_target,
    upstream_update,
):
    checkout, origin = repositories
    release_oid = _release_commit(checkout)
    _git(checkout, 'push', 'origin', 'HEAD:refs/tags/1.0.0')
    if release_target == 'branch':
        _git(checkout, 'push', 'origin', 'HEAD:refs/heads/main')

    if upstream_update:
        upstream = tmp_path / 'upstream'
        _git(tmp_path, 'clone', '-q', '--branch', 'main', origin, upstream)
        _git(upstream, 'config', 'commit.gpgsign', 'false')
        (upstream / 'upstream-only.txt').write_text('concurrent upstream content\n')
        _git(upstream, 'add', 'upstream-only.txt')
        _git(upstream, 'commit', '-qm', 'upstream update')
        _git(upstream, 'push', 'origin', 'HEAD:refs/heads/main')

    remote_tip = _git(origin, 'rev-parse', 'refs/heads/main')
    result = _run_step(checkout, 'prepare-push-bump-commit')
    assert result.returncode == 0, result.stdout + result.stderr

    # Model version's commit-to-head operation, then execute the actual signed-bump preparation.
    (checkout / 'VERSION').write_text('1.1.0-dev\n')
    _git(checkout, 'add', 'VERSION')
    _git(checkout, 'commit', '-qm', 'next version: 1.1.0-dev')
    bump_oid = _git(checkout, 'rev-parse', 'HEAD')
    assert _git(checkout, 'rev-parse', 'HEAD~1') == remote_tip

    output = tmp_path / 'github-output'
    result = _run_step(
        checkout,
        'push-bump-commit',
        GITHUB_OUTPUT=str(output),
        RELEASE_NOTES_PATH='',
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'branch-tip={remote_tip}' in output.read_text().splitlines()
    assert f'source-commit={bump_oid}' in output.read_text().splitlines()
    assert _git(checkout, 'rev-parse', 'HEAD') == bump_oid
    assert _git(checkout, 'rev-parse', f'{bump_oid}~1') == remote_tip
    assert _git(checkout, 'diff', '--name-only', remote_tip, bump_oid) == 'VERSION'
    assert _git(checkout, 'diff', '--cached', '--name-only') == ''
    assert (checkout / 'release-only.txt').exists() == (release_target == 'branch')
    if upstream_update:
        assert (checkout / 'upstream-only.txt').read_text() == 'concurrent upstream content\n'
        assert _git(checkout, 'show', ':upstream-only.txt') == 'concurrent upstream content'
    assert _git(origin, 'rev-parse', 'refs/heads/main') == remote_tip
    assert _git(origin, 'rev-parse', 'refs/tags/1.0.0') == release_oid


def test_prepare_bump_stops_when_fetch_fails(repositories):
    checkout, origin = repositories
    _git(checkout, 'fetch', 'origin', 'refs/heads/main')
    stale_fetch_head = _git(checkout, 'rev-parse', 'FETCH_HEAD')
    release_oid = _release_commit(checkout)
    assert stale_fetch_head != release_oid

    result = _run_step(
        checkout,
        'prepare-push-bump-commit',
        github_ref='refs/heads/missing',
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert _git(checkout, 'rev-parse', 'HEAD') == release_oid
    assert (checkout / 'release-only.txt').read_text() == 'release content\n'
    assert _git(origin, 'rev-parse', 'refs/heads/main') == stale_fetch_head
