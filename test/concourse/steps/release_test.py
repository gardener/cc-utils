import os
import pytest

import git

import gitutil

import concourse.steps.release as release_step
import concourse.model.traits.version as version_trait


@pytest.fixture
def git_helper(tmpdir):
    print(tmpdir)
    repo = git.Repo.init(tmpdir)
    repo.index.commit(message='first (empty) commit')

    git_helper = gitutil.GitHelper(repo=repo, github_cfg=None, github_repo_path='org/repo')

    return git_helper


def test_create_release_commit(git_helper):
    work_tree = git_helper.repo.working_tree_dir
    version_file = os.path.join(work_tree, 'version.txt')
    with open(version_file, 'w') as f:
        f.write('dummy')
    git_helper.add_and_commit('add version')

    release_commit = release_step.create_release_commit(
        git_helper=git_helper,
        branch='master',
        version='1.2.3',
        version_interface=version_trait.VersionInterface.FILE,
        version_path=version_file,
        release_commit_message_prefix='my prefix',
    )

    repo = git_helper.repo

    # check returned commit is contained in repository (-> was created)
    assert 'my prefix' in release_commit.message

    # release-commit should not be visible in working tree
    with open(version_file) as f:
        assert f.read() == 'dummy'

    # check commit actually contains correct (versionfile) diff
    repo.head.reset(release_commit, working_tree=True)
    with open(version_file) as f:
        assert f.read() == '1.2.3'
