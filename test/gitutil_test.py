import git
import os
import pytest

import gitutil


@pytest.fixture
def git_repo(tmpdir):
    repo = git.Repo.init(tmpdir)

    repo.index.commit('first commit')

    return repo


@pytest.fixture
def git_helper(git_repo):
    return gitutil.GitHelper(
        repo=git_repo,
        github_cfg=None,
        github_repo_path=None,
    )


def test_index_to_commit(git_helper):
    repo = git_helper.repo
    repo_dir = repo.working_tree_dir

    original_head_commit = repo.head.commit

    commit = git_helper.index_to_commit('empty commit')

    assert commit.message == 'empty commit'

    # index_to_commit should not change index
    assert repo.head.commit == original_head_commit

    new_file = os.path.join(repo_dir, 'new_file.txt')
    with open(new_file, 'w') as f:
        f.write('new_file')

    commit = git_helper.index_to_commit('add new_file')

    # file should be left in working_tree (but not added to index)
    assert os.path.isfile(new_file)
    assert git_helper.is_dirty
    os.unlink(new_file)
    assert not git_helper.is_dirty

    repo.head.reset(commit, working_tree=True)
    # check "new_file.txt" was actually added to returned commit
    assert os.path.isfile(new_file)
    with open(new_file) as f:
        assert f.read() == 'new_file'

    # add "another" file to initial commit
    another_file = os.path.join(repo_dir, 'another_file')
    with open(another_file, 'w') as f:
        f.write('another_file')

    commit_with_another_file = git_helper.index_to_commit(
        message='add another file',
        parent_commits=(original_head_commit,)
    )

    assert commit_with_another_file.parents == [original_head_commit]

    # try the same, but pass commit-digest
    commit_with_another_file = git_helper.index_to_commit(
        message='add another file',
        parent_commits=(original_head_commit.hexsha,)
    )

    assert commit_with_another_file.parents == [original_head_commit]
