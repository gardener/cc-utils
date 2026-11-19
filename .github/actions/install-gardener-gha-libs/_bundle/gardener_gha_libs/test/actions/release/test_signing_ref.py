# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import os
import pathlib
import subprocess

import pytest
import yaml


ACTION_PATH = pathlib.Path(__file__).resolve().parents[3] / '.github/actions/release'
STEPS = {
    step['name']: step
    for step in yaml.safe_load((ACTION_PATH / 'action.yaml').read_text())['runs']['steps']
    if 'name' in step
}


class SigningRefRepository:
    def __init__(self, root):
        self.root = root
        self.remote = root / 'remote.git'
        self.checkout = root / 'checkout'
        self.output_number = 0
        self.env = {
            **os.environ,
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_SYSTEM': os.devnull,
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_ALLOW_PROTOCOL': 'file',
            'PYTHONDONTWRITEBYTECODE': '1',
        }
        self.git('init', '--bare', '--initial-branch=main', str(self.remote), cwd=root)
        self.git('init', '--initial-branch=main', str(self.checkout), cwd=root)
        self.git('config', 'user.name', 'Signing Ref Test')
        self.git('config', 'user.email', 'signing-ref@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('commit', '--allow-empty', '-m', 'base')
        self.parent = self.git('rev-parse', 'HEAD')
        tree = self.git('rev-parse', 'HEAD^{tree}')
        self.signed = self.git('commit-tree', tree, '-p', self.parent, '-m', 'signed')
        self.unrelated = self.git('commit-tree', tree, '-p', self.signed, '-m', 'unrelated')
        self.git('push', 'origin', f'{self.unrelated}:refs/heads/main')

    def git(self, *args, cwd=None):
        return subprocess.run(
            ['git', *args],
            cwd=cwd or self.checkout,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def ref_oid(self, branch):
        result = self.git('ls-remote', 'origin', f'refs/heads/{branch}')
        return result.split()[0] if result else None

    def move_ref(self, branch, oid):
        self.git('push', '--force', 'origin', f'{oid}:refs/heads/{branch}')

    def fixed_nonce(self):
        # Fix only UUID generation. All action code and Git operations remain real.
        site = self.root / 'python-site'
        site.mkdir()
        (site / 'sitecustomize.py').write_text(
            'import uuid\n'
            'uuid.uuid4 = lambda: uuid.UUID(int=1)\n'
        )
        self.env['PYTHONPATH'] = str(site)

    def run_step(self, name, outputs=None, signed_oid=''):
        outputs = outputs or {}
        context = {
            'github.run_id': '1234',
            'github.run_attempt': '1',
            'steps.release-parent.outputs.parent': self.parent,
            'steps.sign-release-commit.outputs.commit-sha': signed_oid,
            **{
                f'steps.signing-branch.outputs.{key}': value
                for key, value in outputs.items()
            },
        }
        step = STEPS[name]
        env = dict(self.env)
        for key, expression in step.get('env', {}).items():
            assert expression.startswith('${{ ') and expression.endswith(' }}')
            alternatives = expression[4:-3].split(' || ')
            env[key] = next(
                (context.get(key, '') for key in alternatives if context.get(key, '')),
                '',
            )
        self.output_number += 1
        output_file = self.root / f'output-{self.output_number}'
        env['GITHUB_OUTPUT'] = str(output_file)
        env['GITHUB_ACTION_PATH'] = str(ACTION_PATH)
        result = subprocess.run(
            ['bash', '-e', '-o', 'pipefail', '-c', step['run']],
            cwd=self.checkout,
            env=env,
            capture_output=True,
            text=True,
        )
        output = dict(
            line.split('=', 1)
            for line in output_file.read_text().splitlines()
        ) if output_file.exists() else {}
        return result, output

    def create(self):
        return self.run_step('create-release-signing-branch')

    def cleanup(self, outputs, signed_oid=''):
        # This is the composite action's always() guard.
        if not outputs.get('name'):
            return
        result, _ = self.run_step(
            'cleanup-release-signing-branch',
            outputs=outputs,
            signed_oid=signed_oid,
        )
        assert result.returncode == 0, result.stderr


@pytest.fixture
def repository(tmp_path):
    return SigningRefRepository(tmp_path)


def create_successfully(repository):
    result, outputs = repository.create()
    assert result.returncode == 0, result.stderr
    assert outputs['name'].startswith('release-signing/')
    assert repository.ref_oid(outputs['name']) == repository.parent
    return outputs


def test_same_run_and_attempt_have_independent_refs(repository):
    first = create_successfully(repository)
    second = create_successfully(repository)

    assert first['name'] != second['name']
    repository.cleanup(first)
    assert repository.ref_oid(first['name']) is None
    assert repository.ref_oid(second['name']) == repository.parent
    repository.cleanup(second)
    assert repository.ref_oid(second['name']) is None


@pytest.mark.parametrize('existing_oid', ['parent', 'signed', 'unrelated'])
def test_preexisting_ref_is_never_claimed_or_overwritten(repository, existing_oid):
    repository.fixed_nonce()
    first = create_successfully(repository)
    existing_oid = getattr(repository, existing_oid)
    repository.move_ref(first['name'], existing_oid)

    result, second = repository.create()

    assert result.returncode != 0
    assert not second.get('name')
    assert repository.ref_oid(first['name']) == existing_oid
    repository.cleanup(second)
    assert repository.ref_oid(first['name']) == existing_oid


def test_other_new_refs_do_not_prove_signing_ref_ownership(repository):
    repository.fixed_nonce()
    first = create_successfully(repository)
    repository.git('config', 'push.followTags', 'true')
    repository.git('tag', '-a', 'incidental-tag', '-m', 'incidental', repository.parent)

    result, second = repository.create()

    assert result.returncode != 0
    assert not second.get('name')
    assert repository.ref_oid(first['name']) == repository.parent


def test_cleanup_removes_ref_after_expected_signing_move(repository):
    outputs = create_successfully(repository)
    repository.move_ref(outputs['name'], repository.signed)

    repository.cleanup(outputs, signed_oid=repository.signed)

    assert repository.ref_oid(outputs['name']) is None


@pytest.mark.parametrize('signed_oid', ['', 'signed'])
def test_cleanup_preserves_unrelated_update(repository, signed_oid):
    outputs = create_successfully(repository)
    repository.move_ref(outputs['name'], repository.unrelated)

    repository.cleanup(
        outputs,
        signed_oid=getattr(repository, signed_oid) if signed_oid else '',
    )

    assert repository.ref_oid(outputs['name']) == repository.unrelated


def test_cleanup_removes_original_ref_after_signing_failure(repository):
    outputs = create_successfully(repository)

    repository.cleanup(outputs)

    assert repository.ref_oid(outputs['name']) is None


def test_cleanup_is_best_effort_when_ref_is_already_gone(repository):
    outputs = create_successfully(repository)
    repository.git('push', 'origin', f':refs/heads/{outputs["name"]}')

    repository.cleanup(outputs, signed_oid=repository.signed)

    assert repository.ref_oid(outputs['name']) is None
