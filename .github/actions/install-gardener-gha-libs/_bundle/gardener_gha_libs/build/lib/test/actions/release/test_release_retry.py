# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import http.server
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import types

import pytest
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[3]
ACTION = ROOT / '.github/actions/release/action.yaml'
TAG = 'refs/tags/v1.2.3'


def git(repo, *args, check=True):
    return subprocess.run(
        ('git', '-C', str(repo), *args),
        check=check, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def release(tmp_path, monkeypatch):
    # Use actual SSH-signed commits and a file:// remote; no GitHub or OCI writes.
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    key = tmp_path / 'signing-key'
    subprocess.run(
        ('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)), check=True,
    )
    allowed = tmp_path / 'allowed-signers'
    allowed.write_text(f'tester@example.org {key.with_suffix(".pub").read_text()}')
    remote = tmp_path / 'remote.git'
    repo = tmp_path / 'checkout'
    git(tmp_path, 'init', '--bare', '-q', str(remote))
    git(tmp_path, 'clone', '-q', str(remote), str(repo))
    for path in (remote, repo):
        git(path, 'config', 'user.name', 'tester')
        git(path, 'config', 'user.email', 'tester@example.org')
        git(path, 'config', 'commit.gpgsign', 'false')
        git(path, 'config', 'gpg.format', 'ssh')
        git(path, 'config', 'user.signingkey', str(key))
        git(path, 'config', 'gpg.ssh.allowedSignersFile', str(allowed))
    (repo / 'VERSION').write_text('1.2.3-dev\n')
    git(repo, 'add', 'VERSION')
    git(repo, 'commit', '-qm', 'base')
    parent = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
    (repo / 'VERSION').write_text('1.2.3\n')
    git(repo, 'commit', '-qam', 'release 1.2.3')
    unsigned = git(repo, 'rev-parse', 'HEAD')
    tree = git(repo, 'rev-parse', 'HEAD^{tree}')

    state = types.SimpleNamespace(
        path=tmp_path, repo=repo, remote=remote, parent=parent,
        unsigned=unsigned, tree=tree, overrides={}, requests=[], sign_calls=0,
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state.requests.append(self.path)
            prefix = '/repos/example/project/git/commits/'
            assert self.path.startswith(prefix), self.path
            oid = self.path.removeprefix(prefix)
            verified = subprocess.run(
                ('git', '-C', str(remote), 'verify-commit', oid),
                capture_output=True,
            ).returncode == 0
            payload = {
                'sha': oid,
                'verification': {
                    'verified': verified,
                    'reason': 'valid' if verified else 'unsigned',
                    'signature': 'test-signature' if verified else None,
                    'payload': 'test-payload' if verified else None,
                },
            }
            payload.update(state.overrides)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state.api_url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def make_commit(state, *, tree=None, parents=None, signed=True, message='published release'):
    args = ['commit-tree', tree or state.tree]
    for parent in parents if parents is not None else [state.parent]:
        args += ['-p', parent]
    if signed:
        args += ['-S']
    return git(state.repo, *args, '-m', message)


def publish_tag(state, oid, *, annotated=False):
    if annotated:
        git(state.repo, '-c', 'tag.gpgsign=false', 'tag', '-a', '-m', 'release', 'v1.2.3', oid)
        git(state.repo, 'push', '-q', 'origin', TAG)
    else:
        git(state.repo, 'push', '-q', 'origin', f'{oid}:{TAG}')
    return git(state.remote, 'rev-parse', TAG)


def descriptor(unsigned):
    return yaml.safe_dump({
        'meta': {'schemaVersion': 'v2'},
        'component': {
            'name': 'example.com/project', 'version': '1.2.3', 'provider': 'internal',
            'repositoryContexts': [], 'componentReferences': [], 'resources': [],
            'sources': [{
                'name': 'source', 'version': '1.2.3', 'type': 'git',
                'access': {
                    'type': 'github', 'repoUrl': 'github.com/example/project',
                    'ref': TAG, 'commit': unsigned,
                },
            }],
        },
    })


class ReleaseRun:
    '''Execute the relevant real composite-action steps, replacing only external mutations.'''
    names = {
        'write-component-descriptor-from-input', 'read-release-tag',
        'reuse-existing-release-commit', 'read-release-commit-message',
        'capture-release-commit-for-signing', 'create-release-signing-branch',
        'sign-release-commit', 'save-release-commit-digest', 'preprocess',
        'patch-descriptor-main-source-commit', 'publish OCM Component-Descriptor', 'push tag-ref',
    }

    def __init__(self, state):
        self.state = state
        self.outputs = {}
        self.published = None
        self.failed_step = None
        self.error = ''
        self.inputs = {
            key: value.get('default', '')
            for key, value in yaml.safe_load(ACTION.read_text())['inputs'].items()
        }
        self.inputs.update({
            'github-tag-template': 'v${version}',
            'component-descriptor': descriptor(state.unsigned),
            'github-token': 'local-test-token',
            'on-existing-component-descriptor': 'overwrite',
        })

    def expression(self, expr):
        expr = expr.strip()
        if expr.startswith('${{'):
            expr = expr[3:-2].strip()
        if ' || ' in expr:
            return next((value for part in expr.split(' || ')
                         if (value := self.expression(part))), '')
        for operator in (' == ', ' != '):
            if operator in expr:
                left, right = expr.split(operator)
                equal = self.expression(left) == self.expression(right)
                return equal if operator == ' == ' else not equal
        if expr.startswith("'") and expr.endswith("'"):
            return expr[1:-1]
        if expr.startswith('inputs.'):
            return self.inputs[expr.removeprefix('inputs.')]
        if expr.startswith('steps.'):
            _, step, _, key = expr.split('.')
            return self.outputs.get(step, {}).get(key, '')
        return {'github.run_id': '1234', 'github.run_attempt': '1'}.get(expr, '')

    def render(self, text):
        return re.sub(r'\$\{\{(.*?)\}\}', lambda m: str(self.expression(m[1])), text)

    def run(self):
        state = self.state
        # yq is a runner dependency, not part of the action under test.
        bin_dir = state.path / 'bin'
        bin_dir.mkdir(exist_ok=True)
        yq = bin_dir / 'yq'
        yq.write_text(
            f'#!{sys.executable}\nimport sys, yaml\n'
            "print(yaml.safe_load(open(sys.argv[2]))['component']['version'])\n"
        )
        yq.chmod(0o755)
        for step in yaml.safe_load(ACTION.read_text())['runs']['steps']:
            name = step.get('name')
            if name not in self.names:
                continue
            if 'if' in step and not self.expression(step['if']):
                continue
            if name == 'sign-release-commit':
                # Model only the GitHub mutation: sign the immutable source on the given branch.
                state.sign_calls += 1
                parent = self.render(step['with']['expected-head-oid'])
                source = self.render(step['with']['source-commit'])
                branch = self.render(step['with']['branch'])
                assert source == state.unsigned
                assert git(state.repo, 'rev-parse', f'{source}~1') == parent
                assert git(state.remote, 'rev-parse', f'refs/heads/{branch}') == parent
                oid = make_commit(
                    state, tree=git(state.repo, 'rev-parse', f'{source}^{{tree}}'),
                    parents=[parent],
                    message=f'new signed commit {state.sign_calls}',
                )
                git(state.repo, 'push', '-q', 'origin', f'{oid}:refs/heads/{branch}')
                self.outputs[step['id']] = {'commit-sha': oid}
                continue
            if name == 'publish OCM Component-Descriptor':
                self.published = yaml.safe_load((state.path / 'descriptor.yaml').read_text())
                continue
            output = state.path / 'step-output'
            output.write_text('')
            env = dict(os.environ) | {
                'PATH': f'{bin_dir}:{pathlib.Path(sys.executable).parent}:{os.environ["PATH"]}',
                'PYTHONPATH': str(ROOT),
                'PYTHONDONTWRITEBYTECODE': '1',
                'GITHUB_ACTION_PATH': str(ACTION.parent),
                'GITHUB_OUTPUT': str(output),
                'GITHUB_API_URL': state.api_url,
                'GITHUB_REPOSITORY': 'example/project',
            } | {key: self.render(value) for key, value in step.get('env', {}).items()}
            script = self.render(step['run']).replace(
                '/tmp/component-descriptor.yaml', str(state.path / 'descriptor.yaml'),
            ).replace('/tmp/assets.yaml', str(state.path / 'assets.yaml'))
            command = (sys.executable, '-c', script) if step['shell'] == 'python' else (
                'bash', '-e', '-o', 'pipefail', '-c', script,
            )
            result = subprocess.run(
                command, cwd=state.repo, env=env, capture_output=True, text=True,
            )
            if result.returncode:
                self.failed_step, self.error = name, result.stdout + result.stderr
                return self
            if 'id' in step:
                lines = iter(output.read_text().splitlines())
                values = {}
                for line in lines:
                    if '<<' in line:
                        key, delimiter = line.split('<<', 1)
                        contents = []
                        while (line := next(lines)) != delimiter:
                            contents.append(line)
                        values[key] = '\n'.join(contents)
                    else:
                        key, value = line.split('=', 1)
                        values[key] = value
                self.outputs[step['id']] = values
        return self

    @property
    def digest(self):
        return self.outputs['save-release-commit-digest']['digest']

    @property
    def descriptor_commit(self):
        return self.published['component']['sources'][0]['access']['commit']


def test_first_release_then_retry_keeps_published_digest(release):
    first = ReleaseRun(release).run()
    assert first.failed_step is None, first.error
    assert release.sign_calls == 1
    assert first.digest != release.unsigned
    assert first.descriptor_commit == first.digest
    assert git(release.remote, 'rev-parse', TAG) == first.digest
    # Emulate a fresh release job checking out the original imported commit.
    git(release.repo, 'reset', '--hard', release.unsigned)
    git(
        release.remote, 'update-ref', '-d',
        f'refs/heads/{first.outputs["signing-branch"]["name"]}', first.digest,
    )
    retry = ReleaseRun(release).run()
    assert retry.failed_step is None, retry.error
    assert release.sign_calls == 1
    assert retry.digest == retry.descriptor_commit == first.digest
    assert git(release.remote, 'rev-parse', TAG) == first.digest
    assert not git(release.remote, 'for-each-ref', 'refs/heads/release-signing/')


@pytest.mark.parametrize('annotated', (False, True))
def test_matching_published_tag_is_reused_without_moving_refs(release, annotated):
    signed = make_commit(release)
    tag_object = publish_tag(release, signed, annotated=annotated)
    # A stale local tag must neither mask the remote tag nor be overwritten by fetch.
    git(release.repo, 'update-ref', TAG, release.unsigned)
    run = ReleaseRun(release).run()
    assert run.failed_step is None, run.error
    assert run.digest == run.descriptor_commit == signed
    assert release.sign_calls == 0
    assert release.requests == [f'/repos/example/project/git/commits/{signed}']
    assert git(release.repo, 'rev-parse', TAG) == release.unsigned
    assert git(release.remote, 'rev-parse', TAG) == tag_object
    assert not git(release.remote, 'for-each-ref', 'refs/heads/release-signing/')


@pytest.mark.parametrize('mismatch', ('tree', 'parent', 'merge', 'unsigned', 'unverified', 'sha'))
def test_unacceptable_tag_fails_before_descriptor_overwrite(release, mismatch):
    kwargs = {}
    if mismatch == 'tree':
        kwargs['tree'] = git(release.repo, 'rev-parse', f'{release.parent}^{{tree}}')
    if mismatch == 'parent':
        kwargs['parents'] = [release.unsigned]
    if mismatch == 'merge':
        kwargs['parents'] = [release.parent, release.unsigned]
    if mismatch == 'unsigned':
        kwargs['signed'] = False
    signed = make_commit(release, **kwargs)
    tag_object = publish_tag(release, signed)
    if mismatch == 'unverified':
        release.overrides['verification'] = {'verified': False, 'reason': 'bad_email'}
    if mismatch == 'sha':
        release.overrides['sha'] = release.unsigned
    run = ReleaseRun(release).run()
    assert run.published is None, 'descriptor was overwritten before tag validation'
    assert run.failed_step == 'reuse-existing-release-commit', run.error
    assert release.sign_calls == 0
    assert git(release.repo, 'rev-parse', 'HEAD') == release.unsigned
    assert git(release.remote, 'rev-parse', TAG) == tag_object
    assert not git(release.remote, 'for-each-ref', 'refs/heads/release-signing/')
