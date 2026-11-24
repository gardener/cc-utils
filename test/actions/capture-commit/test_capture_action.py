# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import base64
import os
import pathlib
import re
import subprocess

import pytest
import yaml


_ACTION_DIR = pathlib.Path(__file__).resolve().parents[3] / '.github/actions/capture-commit'
with (_ACTION_DIR / 'action.yaml').open() as f:
    _ACTION = yaml.safe_load(f)


def _git(repo, *args):
    return subprocess.run(
        ('git', '-C', str(repo), *args), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 't@example.org')
    _git(repo, 'config', 'user.name', 'tester')
    _git(repo, 'config', 'commit.gpgsign', 'false')
    _git(repo, 'config', 'core.excludesfile', os.devnull)
    (repo / 'VERSION').write_text('1.0.0\n')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'release')
    return repo


def _run_action_step(repo, step, env, inputs=None, check=True):
    inputs = {
        'timestamp-reference': '',
        'commit': '',
        'commit-summary': 'none',
        'to-artefact': 'captured-commit',
        **(inputs or {}),
    }
    script = re.sub(
        r'\$\{\{\s*inputs\.([\w-]+)\s*\}\}',
        lambda match: inputs[match[1]],
        step['run'],
    )
    return subprocess.run(
        ('bash', '-c', script), cwd=repo, env=env, check=check,
        capture_output=True, text=True,
    )


def _capture(repo, tmp_path, *, inputs=None, check=True):
    invocation = tmp_path / f'invocation-{len(list(tmp_path.glob("invocation-*")))}'
    invocation.mkdir()
    runner_temp = invocation / 'runner temp'
    runner_temp.mkdir()
    env = dict(
        os.environ,
        GITHUB_ACTION_PATH=str(_ACTION_DIR),
        GITHUB_OUTPUT=str(invocation / 'output'),
        GITHUB_STEP_SUMMARY=str(invocation / 'summary'),
        RUNNER_TEMP=str(runner_temp),
    )
    # The production action targets Ubuntu. Use real GNU tools when running it on macOS.
    gnubin = pathlib.Path('/opt/homebrew/opt/coreutils/libexec/gnubin')
    if gnubin.is_dir():
        env['PATH'] = f'{gnubin}{os.pathsep}{env["PATH"]}'

    steps = _ACTION['runs']['steps']
    result = _run_action_step(repo, steps[0], env, inputs=inputs, check=check)
    output_file = invocation / 'output'
    outputs = dict(
        line.split('=', 1) for line in output_file.read_text().splitlines()
    ) if output_file.exists() else {}

    # Exercise the real upload-artifact contract without making a remote upload.
    upload = steps[1]
    assert upload['with']['name'] == '${{ inputs.to-artefact }}'
    assert upload['with']['path'] == 'commit-objects.bundle'
    bundle = repo / upload['with']['path']
    artifact = invocation / 'commit-objects.bundle'
    if bundle.exists():
        artifact.write_bytes(bundle.read_bytes())
        if result.returncode == 0:
            _run_action_step(repo, steps[2], env)
            assert not bundle.exists()

    return result, outputs, artifact, runner_temp


def _bump(repo, version):
    (repo / 'VERSION').write_text(version + '\n')
    # The version action intentionally stages all callback/version changes.
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '--allow-empty', '-qm', f'next version: {version}')
    return _git(repo, 'rev-parse', 'HEAD')


def test_repeated_release_bumps_do_not_capture_digest_artifact(checkout, tmp_path):
    release = _git(checkout, 'rev-parse', 'HEAD')
    source_bump = _bump(checkout, '1.1.0-dev')
    _, first, first_bundle, first_temp = _capture(checkout, tmp_path)
    assert first['commit-digest'] == source_bump
    assert base64.b64decode(first['commit-objects']) == first_bundle.read_bytes()

    # This is the optional release-branch path after signing the source-branch bump.
    # Resetting tracked files does not remove an untracked capture helper.
    _git(checkout, 'reset', '--hard', release)
    branch_bump = _bump(checkout, '1.0.1-dev')
    _, second, second_bundle, second_temp = _capture(checkout, tmp_path)
    assert second['commit-digest'] == branch_bump
    assert base64.b64decode(second['commit-objects']) == second_bundle.read_bytes()
    assert _git(checkout, 'rev-parse', 'refs/capture-commit') == branch_bump

    # Import the actual published bundle into another real repository and inspect its source tree.
    receiver = tmp_path / 'receiver'
    receiver.mkdir()
    _git(receiver, 'init', '-q')
    _git(receiver, 'fetch', str(second_bundle), 'refs/capture-commit')
    assert _git(receiver, 'rev-parse', 'FETCH_HEAD') == branch_bump
    assert _git(receiver, 'ls-tree', '-r', '--name-only', 'FETCH_HEAD') == 'VERSION'
    assert _git(receiver, 'show', 'FETCH_HEAD:VERSION') == '1.0.1-dev'
    assert not (checkout / 'commit-digest').exists()
    assert list(first_temp.iterdir()) == list(second_temp.iterdir()) == []


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('summary', ['none', 'short', 'full'])
def test_capture_preserves_caller_owned_digest(checkout, tmp_path, tracked, summary):
    caller_file = checkout / 'commit-digest'
    caller_content = b'caller-owned contents\n'
    caller_file.write_bytes(caller_content)
    if tracked:
        _git(checkout, 'add', 'commit-digest')
        _git(checkout, 'commit', '-qm', 'caller-owned digest')
    expected = _git(checkout, 'rev-parse', 'HEAD')
    status_before = _git(checkout, 'status', '--porcelain=v1', '-z')
    _, outputs, artifact, runner_temp = _capture(
        checkout, tmp_path, inputs={'commit-summary': summary, 'commit': expected},
    )
    assert outputs['commit-digest'] == expected
    assert base64.b64decode(outputs['commit-objects']) == artifact.read_bytes()
    assert caller_file.read_bytes() == caller_content
    assert _git(checkout, 'status', '--porcelain=v1', '-z') == status_before
    assert list(runner_temp.iterdir()) == []
    if summary != 'none':
        assert f'commit-digest: `{expected}`' in (artifact.parent / 'summary').read_text()


@pytest.mark.parametrize('failure', ['capture', 'summary'])
def test_capture_failure_cleans_only_invocation_owned_digest(checkout, tmp_path, failure):
    caller_file = checkout / 'commit-digest'
    caller_file.write_text('do not overwrite or remove\n')
    if failure == 'capture':
        _git(checkout, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        inputs = {}
    else:
        inputs = {'commit-summary': 'invalid'}
    result, _, _, runner_temp = _capture(checkout, tmp_path, inputs=inputs, check=False)
    assert result.returncode != 0
    assert caller_file.read_text() == 'do not overwrite or remove\n'
    assert list(runner_temp.iterdir()) == []


def test_large_bundle_keeps_artifact_without_inline_output(checkout, tmp_path):
    (checkout / 'binary').write_bytes(os.urandom(100_000))
    _git(checkout, 'add', 'binary')
    _git(checkout, 'commit', '-qm', 'large payload')
    expected = _git(checkout, 'rev-parse', 'HEAD')
    _, outputs, artifact, runner_temp = _capture(checkout, tmp_path)
    assert artifact.stat().st_size >= 65536
    assert outputs == {'commit-digest': expected}
    assert _git(checkout, 'bundle', 'list-heads', str(artifact)) == (
        f'{expected} refs/capture-commit'
    )
    assert not (checkout / 'commit-digest').exists()
    assert list(runner_temp.iterdir()) == []
