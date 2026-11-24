# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import base64
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import types
import unittest.mock as mock

import pytest
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/release.yaml').read_text())
STEPS = WORKFLOW['jobs']['release-and-bump']['steps']
CONDITION = (
    "${{ inputs.create-release-branch && "
    "steps.prep-next-version.outputs.next-version != 'bump-patch' }}"
)
OPTIONAL_STEPS = STEPS[
    next(i for i, s in enumerate(STEPS) if s.get('name') == 'reset-repository-to-release-commit'):
    next(i for i, s in enumerate(STEPS) if s.get('name') == 'cleanup-stale-release-branches')
]


def _step(name):
    return next(s for s in OPTIONAL_STEPS if s.get('name') == name)


def _signing_step():
    matches = [
        s for s in OPTIONAL_STEPS
        if s.get('uses') == 'gardener/cc-utils/.github/actions/signed-commit@master'
    ]
    assert len(matches) == 1, 'optional release branch must publish through signed-commit'
    return matches[0]


def _render(value, context):
    def replace(match):
        for expression in match[1].strip().split(' || '):
            result = context.get(expression, '')
            if result:
                return result
        return ''

    return re.sub(r'\$\{\{(.*?)\}\}', replace, str(value))


def _git(repo, *args, env=None, input=None):
    return subprocess.run(
        ['git', '-C', str(repo), *args],
        env=env, input=input, check=True, capture_output=True,
    ).stdout.decode().strip()


def _commit(repo, message):
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', message)
    return _git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / 'repo'
    remote = tmp_path / 'origin.git'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.name', 'tester')
    _git(repo, 'config', 'user.email', 'tester@example.org')
    _git(repo, 'config', 'commit.gpgsign', 'false')
    _git(repo, 'init', '--bare', '-q', str(remote))
    _git(repo, 'remote', 'add', 'origin', str(remote))
    (repo / 'VERSION').write_text('v1.2.2\n')
    base = _commit(repo, 'base')
    (repo / 'VERSION').write_text('v1.2.3\n')
    (repo / 'obsolete').write_text('remove in callback\n')
    release = _commit(repo, 'signed release fixture')
    _git(repo, 'push', '-q', 'origin', f'{release}:refs/heads/main')
    context = {
        'steps.component-descriptor.outputs.version': 'v1.2.3',
        'steps.release.outputs.release-commit-digest': release,
        'steps.fed-token.outputs.token': 'installation-token',
        'secrets.GITHUB_TOKEN': 'fallback-token',
        'github.repository': 'o/r',
    }
    return types.SimpleNamespace(
        repo=repo, remote=remote, base=base, release=release, context=context,
        tmp=tmp_path,
    )


def _run_step(repository, step, check=True):
    output = repository.tmp / 'github-output'
    output.write_text('')
    env = dict(os.environ, GITHUB_OUTPUT=str(output))
    env.update({
        key: _render(value, repository.context)
        for key, value in step.get('env', {}).items()
    })
    result = subprocess.run(
        ['bash', '-e', '-o', 'pipefail', '-c', _render(step['run'], repository.context)],
        cwd=repository.repo, env=env, capture_output=True, text=True, check=check,
    )
    if 'id' in step:
        for line in output.read_text().splitlines():
            key, value = line.split('=', 1)
            repository.context[f"steps.{step['id']}.outputs.{key}"] = value
    return result


def _bump(repository):
    step = _step('create-release-branch-bump-commit')
    inputs = step['with']
    subprocess.run([
        sys.executable, str(ROOT / '.github/actions/version/version_cli.py'),
        '--root-dir', str(repository.repo),
        '--operation', inputs['version-operation'],
        '--prerelease', inputs['prerelease'],
    ], cwd=repository.repo, check=True, capture_output=True)
    version = (repository.repo / 'VERSION').read_text().strip()
    # Model a callback's output using real files/objects, including deletion and binary data.
    (repository.repo / 'obsolete').unlink()
    (repository.repo / 'generated.bin').write_bytes(b'\x00\xffcallback')
    source = _commit(repository.repo, inputs['commit-message'].replace('${version}', version))
    if 'id' in step:
        repository.context[f"steps.{step['id']}.outputs.version"] = version
        repository.context[f"steps.{step['id']}.outputs.commit-digest"] = source
    return source


def _initialise(repository, check=True):
    return _run_step(repository, _step('create-and-push-release-branch'), check)


def _remote_head(repository, branch='release-v1.2'):
    return _git(repository.remote, 'rev-parse', f'refs/heads/{branch}')


def test_optional_branch_preserves_version_contract_and_signing_inputs(repository):
    bump = _step('create-release-branch-bump-commit')
    assert bump['with'] == {
        'versionfile': '${{ inputs.version-versionfile }}',
        'read-callback': '${{ inputs.version-read-callback }}',
        'write-callback': '${{ inputs.version-write-callback }}',
        'callback-action-path': '${{ inputs.next-version-callback-action-path }}',
        'commit-message': 'next version: ${version}',
        'version-operation': 'bump-patch',
        'prerelease': 'dev',
        'repository-operation': 'commit-to-head',
        'commit-kind': 'bump',
    }
    source = _bump(repository)
    signing = _signing_step()
    assert all(s['if'] == CONDITION for s in OPTIONAL_STEPS)
    assert OPTIONAL_STEPS.index(bump) < OPTIONAL_STEPS.index(
        _step('create-and-push-release-branch')
    ) < OPTIONAL_STEPS.index(signing)
    _initialise(repository)
    inputs = {k: _render(v, repository.context) for k, v in signing['with'].items()}
    assert inputs['branch'] == 'release-v1.2'
    assert inputs['expected-head-oid'] == repository.release
    assert inputs['source-commit'] == source
    assert inputs['message'] == 'next version: v1.2.4-dev'
    assert not inputs.get('paths')
    assert inputs['token'] == 'installation-token'
    repository.context['steps.fed-token.outputs.token'] = ''
    assert _render(signing['with']['token'], repository.context) == 'fallback-token'


@pytest.mark.parametrize(('version', 'template', 'branch'), [
    ('v1.2.3', None, 'release-v1.2'),
    ('1.2.3', None, 'release-v1.2'),
    ('v1.2.3', 'hotfix-${major}.${minor}.${patch}', 'hotfix-1.2.3'),
])
def test_initialises_final_ref_at_signed_release_not_unsigned_bump(
    repository, version, template, branch,
):
    repository.context['steps.component-descriptor.outputs.version'] = version
    source = _bump(repository)
    if template:
        info = repository.repo / '.ocm/branch-info.yaml'
        info.parent.mkdir()
        info.write_text(yaml.safe_dump({'release-branch-template': template}))
    _initialise(repository)
    assert _remote_head(repository, branch) == repository.release
    assert _git(repository.repo, 'rev-parse', 'HEAD') == source
    assert source not in _git(repository.remote, 'rev-list', '--all').splitlines()


@pytest.mark.parametrize('existing', ['base', 'release', 'source'])
def test_initialisation_never_overwrites_an_existing_different_ref(repository, existing):
    source = _bump(repository)
    existing_oid = source if existing == 'source' else getattr(repository, existing)
    _git(repository.repo, 'push', '-q', 'origin', f'{existing_oid}:refs/heads/release-v1.2')
    result = _initialise(repository, check=False)
    assert (result.returncode == 0) == (existing == 'release'), result.stderr
    assert _remote_head(repository) == existing_oid


def _load_signing_module():
    spec = importlib.util.spec_from_file_location(
        'release_branch_signed_commit',
        ROOT / '.github/actions/signed-commit/signed_commit.py',
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _github_api(repository, tmp_path):
    '''External GraphQL boundary backed by the real bare Git repository.'''
    session = mock.Mock()

    def post(url, json):
        assert url == 'https://api.github.com/graphql'
        inp = json['variables']['input']
        branch = inp['branch']['branchName']
        assert inp['branch']['repositoryNameWithOwner'] == 'o/r'
        parent = inp['expectedHeadOid']
        response = mock.Mock()
        if _remote_head(repository, branch) != parent:
            response.json.return_value = {'errors': [{'message': 'Expected branch head changed'}]}
            return response
        env = dict(
            os.environ, GIT_INDEX_FILE=str(tmp_path / 'api-index'),
            GIT_AUTHOR_NAME='GitHub', GIT_AUTHOR_EMAIL='noreply@example.org',
            GIT_COMMITTER_NAME='GitHub', GIT_COMMITTER_EMAIL='noreply@example.org',
        )
        _git(repository.remote, 'read-tree', parent, env=env)
        for deletion in inp['fileChanges']['deletions']:
            _git(repository.remote, 'update-index', '-z', '--index-info', env=env,
                 input=f"0 {'0' * 40}\t{deletion['path']}\0".encode())
        for addition in inp['fileChanges']['additions']:
            blob = _git(repository.remote, 'hash-object', '-w', '--stdin', env=env,
                        input=base64.b64decode(addition['contents']))
            _git(repository.remote, 'update-index', '--add', '--cacheinfo',
                 '100644', blob, addition['path'], env=env)
        tree = _git(repository.remote, 'write-tree', env=env)
        oid = _git(repository.remote, 'commit-tree', tree, '-p', parent, '-m',
                   inp['message']['headline'], env=env)
        _git(repository.remote, 'update-ref', f'refs/heads/{branch}', oid, parent)
        response.json.return_value = {
            'data': {'createCommitOnBranch': {'commit': {
                'oid': oid,
                'tree': {'oid': tree},
                'signature': {
                    'isValid': True,
                    'state': 'VALID',
                    'wasSignedByGitHub': True,
                },
            }}},
        }
        return response

    session.post.side_effect = post
    return types.SimpleNamespace(session=session)


@pytest.mark.parametrize('concurrent_update', [False, True])
def test_signs_exact_bump_tree_and_rejects_concurrent_ref_update(
    repository, tmp_path, monkeypatch, concurrent_update,
):
    signing = _signing_step()
    _run_step(repository, _step('reset-repository-to-release-commit'))
    source = _bump(repository)
    _initialise(repository)
    assert _remote_head(repository) == repository.release
    inputs = {k: _render(v, repository.context) for k, v in signing['with'].items()}
    # Neither worktree dirt nor a later HEAD may replace the captured bump.
    (repository.repo / 'unrelated').write_text('not part of the bump\n')
    later = _commit(repository.repo, 'unrelated later commit')
    (repository.repo / 'VERSION').write_text('dirty workspace\n')
    if concurrent_update:
        _git(repository.repo, 'push', '-q', 'origin', f'{later}:refs/heads/release-v1.2')
    sc = _load_signing_module()
    monkeypatch.chdir(repository.repo)
    monkeypatch.delenv('GITHUB_OUTPUT', raising=False)
    monkeypatch.setattr(sys, 'argv', [
        'signed_commit.py', '--repository', 'o/r',
        '--branch', inputs['branch'], '--token', inputs['token'],
        '--message', inputs['message'],
        '--expected-head-oid', inputs['expected-head-oid'],
        '--source-commit', inputs['source-commit'],
    ])
    monkeypatch.setattr(sc.github, 'host_org_and_repo', lambda **kw: ('github.com', 'o', 'r'))
    monkeypatch.setattr(sc.github, 'github_api', lambda **kw: _github_api(repository, tmp_path))
    if concurrent_update:
        with pytest.raises(RuntimeError, match='Expected branch head changed'):
            sc.main()
        assert _remote_head(repository) == later
        return
    sc.main()
    signed = _remote_head(repository)
    assert signed != source
    assert _git(repository.remote, 'rev-parse', f'{signed}^') == repository.release
    assert _git(repository.remote, 'rev-parse', f'{signed}^{{tree}}') == _git(
        repository.repo, 'rev-parse', f'{source}^{{tree}}',
    )
    assert _git(repository.remote, 'show', f'{signed}:VERSION') == 'v1.2.4-dev'
    assert _git(repository.remote, 'log', '-1', '--format=%s', signed) == (
        'next version: v1.2.4-dev'
    )
    assert source not in _git(repository.remote, 'rev-list', '--all').splitlines()
