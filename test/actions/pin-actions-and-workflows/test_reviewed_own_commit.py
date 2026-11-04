# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys

import git
import pytest

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'pin-actions-and-workflows')
    ),
)

import pin


def _commit(repo: git.Repo, files: dict[str, str], message: str) -> git.Commit:
    for path, content in files.items():
        abs_path = os.path.join(repo.working_tree_dir, path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w') as f:
            f.write(content)
        repo.index.add([path])
    return repo.index.commit(message)


@pytest.fixture
def repo(tmp_path):
    repo = git.Repo.init(tmp_path, initial_branch='master')
    with repo.config_writer() as cfg:
        cfg.set_value('user', 'name', 'test')
        cfg.set_value('user', 'email', 'test@example.com')
        cfg.set_value('commit', 'gpgsign', 'false')
    _commit(
        repo,
        {
            'VERSION': '1.0.0-dev\n',
            'ocm/VERSION': '1.0.0-dev\n',
            '.github/actions/foo/action.yaml': 'runs: {using: composite, steps: []}\n',
        },
        'initial',
    )
    return repo


def _check(repo, own_commit):
    pin.check_own_commit_reviewed(
        repo=repo,
        own_commit=own_commit,
        allowed_base='master',
    )


def test_commit_on_base_is_accepted(repo):
    _check(repo, repo.head.commit)


def test_ancestor_of_base_is_accepted(repo):
    old = repo.head.commit
    _commit(repo, {'README': 'hi\n'}, 'docs')
    _check(repo, old)


def test_release_commit_on_top_of_base_is_accepted(repo):
    repo.git.checkout('--detach')
    release_commit = _commit(
        repo,
        {'VERSION': '1.0.0\n', 'ocm/VERSION': '1.0.0\n'},
        'release 1.0.0',
    )
    _check(repo, release_commit)


def test_unmerged_branch_is_refused(repo):
    repo.git.checkout('-b', 'feature')
    feature_commit = _commit(
        repo,
        {'.github/actions/foo/action.yaml': 'runs: {using: composite, steps: [evil]}\n'},
        'evil',
    )
    with pytest.raises(pin.UnreviewedOwnCommit):
        _check(repo, feature_commit)


def test_unmerged_change_disguised_as_release_commit_is_refused(repo):
    repo.git.checkout('--detach')
    release_commit = _commit(
        repo,
        {
            'VERSION': '1.0.0\n',
            '.github/actions/foo/action.yaml': 'runs: {using: composite, steps: [evil]}\n',
        },
        'release 1.0.0',
    )
    with pytest.raises(pin.UnreviewedOwnCommit, match='.github/actions/foo/action.yaml'):
        _check(repo, release_commit)


def test_release_commit_on_top_of_unmerged_commit_is_refused(repo):
    repo.git.checkout('-b', 'feature')
    _commit(repo, {'README': 'unreviewed\n'}, 'unreviewed')
    release_commit = _commit(repo, {'VERSION': '1.0.0\n'}, 'release 1.0.0')
    with pytest.raises(pin.UnreviewedOwnCommit, match='nor its parent'):
        _check(repo, release_commit)


def test_merge_commit_not_on_base_is_refused(repo):
    base = repo.head.commit
    repo.git.checkout('-b', 'feature')
    _commit(repo, {'README': 'feature\n'}, 'feature')
    repo.git.checkout('-b', 'other', base.hexsha)
    _commit(repo, {'OTHER': 'other\n'}, 'other')
    repo.git.merge('feature', '--no-edit')
    with pytest.raises(pin.UnreviewedOwnCommit, match='2 parents'):
        _check(repo, repo.head.commit)


def test_create_pinned_branch_refuses_before_touching_refs(repo):
    repo.git.checkout('-b', 'feature')
    _commit(repo, {'README': 'unreviewed\n'}, 'unreviewed')

    with pytest.raises(pin.UnreviewedOwnCommit):
        pin.create_pinned_branch(
            repo_root=repo.working_tree_dir,
            own_ref='feature',
            target_branch='v1',
            own_org='myorg',
            own_repo='myrepo',
            allowed_base='master',
        )

    assert 'v1' not in [head.name for head in repo.heads]
    assert not repo.tags
