# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import subprocess
import unittest.mock

import pytest

import github.pullrequest as gpr
import gitutil


def _git(path, *args):
    return subprocess.run(
        ('git', '-C', str(path), *args), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def git_helper(tmp_path):
    path = str(tmp_path)
    _git(path, 'init', '-q')
    _git(path, 'config', 'user.email', 't@example.org')
    _git(path, 'config', 'user.name', 'tester')
    _git(path, 'config', 'commit.gpgsign', 'false')
    (tmp_path / 'VERSION').write_text('1.0.0\n')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-qm', 'init')
    # leave an uncommitted change in the worktree
    (tmp_path / 'VERSION').write_text('2.0.0\n')
    return gitutil.GitHelper(repo=path, git_cfg=gitutil.GitCfg(repo_url=f'file://{path}'))


@pytest.fixture
def repo_mock():
    m = unittest.mock.Mock()
    m.owner.login = 'o'
    m.name = 'r'
    return m


def test_commit_and_push_signs_onto_created_branch(git_helper, repo_mock):
    parent_sha = git_helper.repo.head.commit.hexsha
    sign_calls = []

    def fake_sign(**kw):
        sign_calls.append(kw)
        return 'signedsha'

    with gpr.commit_and_push_to_tmp_branch(
        repository=repo_mock, git_helper=git_helper, commit_message='Upgrade x',
        github_token='t', sign=fake_sign,
    ) as branch:
        assert branch.startswith('ci-')

    repo_mock.create_ref.assert_called_once()
    assert sign_calls[0]['branch'] == branch
    assert sign_calls[0]['expected_head_oid'] == parent_sha
    assert sign_calls[0]['repository_full_name'] == 'o/r'
    assert sign_calls[0]['token'] == 't'
    assert sign_calls[0]['message'] == 'Upgrade x'


def test_commit_and_push_purges_branch_on_sign_failure(git_helper, repo_mock):
    def boom(**kw):
        raise RuntimeError('sign failed')

    with pytest.raises(RuntimeError):
        with gpr.commit_and_push_to_tmp_branch(
            repository=repo_mock, git_helper=git_helper, commit_message='m',
            github_token='t', sign=boom):
            pass

    repo_mock.ref.return_value.delete.assert_called()


def test_commit_and_push_sign_failure_not_masked_by_cleanup_error(git_helper, repo_mock):
    # cleanup delete() raising must not replace the original signing error
    repo_mock.ref.return_value.delete.side_effect = RuntimeError('cleanup 404')

    def boom(**kw):
        raise ValueError('signing failed: no valid GitHub signature')

    with pytest.raises(ValueError, match='no valid GitHub signature'):
        with gpr.commit_and_push_to_tmp_branch(
            repository=repo_mock, git_helper=git_helper, commit_message='m',
            github_token='t', sign=boom):
            pass

    repo_mock.ref.return_value.delete.assert_called()
