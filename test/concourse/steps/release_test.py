import pytest
from unittest.mock import MagicMock
from github.util import GitHubRepositoryHelper, GitHubRepoBranch
from github.release_notes.util import ReleaseNotes

import concourse.steps.release
from concourse.model.traits.release import (
    ReleaseCommitPublishingPolicy,
)


class TestReleaseCommitStep(object):
    @pytest.fixture()
    def examinee(self, tmp_path):
        # create required temporary file relative to the provided temporary directory
        temporary_version_file = tmp_path.joinpath('version_file')
        temporary_version_file.touch()

        def _examinee(
            repo_dir=str(tmp_path),
            release_version='1.0.0',
            repository_version_file_path='version_file',
            repository_branch='master',
            release_commit_callback=None,
            ):
            return concourse.steps.release.ReleaseCommitStep(
                git_helper=MagicMock(),
                repo_dir=repo_dir,
                release_version=release_version,
                repository_version_file_path=repository_version_file_path,
                repository_branch=repository_branch,
                release_commit_message_prefix=None,
                release_commit_callback=release_commit_callback,
                publishing_policy=ReleaseCommitPublishingPolicy.TAG_ONLY,
            )
        return _examinee

    def test_validation(self, examinee, tmp_path):
        # create temporary files in the provided directory
        temporary_callback_file = tmp_path.joinpath('callback_script')
        temporary_callback_file.touch()
        examinee(
            release_commit_callback='callback_script',
        ).validate()

    def test_validation_fail_on_missing_release_callback_script(self, examinee, tmp_path):
        with pytest.raises(ValueError):
            # pass non-existing relative file-name
            examinee(release_commit_callback='no_such_file').validate()

    def test_validation_fail_on_missing_version_file(self, examinee, tmp_path):
        with pytest.raises(ValueError):
            examinee(repository_version_file_path='no_such_file').validate()

    def test_validation_fail_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()


class NextDevCycleCommitStep(object):
    @pytest.fixture()
    def examinee(self, tmp_path):
        # create required temporary file relative to the provided temporary directory
        temporary_version_file = tmp_path.joinpath('version_file')
        temporary_version_file.touch()

        def _examinee(
            repo_dir=str(tmp_path),
            release_version='1.0.0',
            repository_version_file_path='version_file',
            repository_branch='master',
            version_operation='bump_minor',
            prerelease_suffix='dev',
            dev_cycle_commit_callback=None,
            ):
            return concourse.steps.release.NextDevCycleCommitStep(
                git_helper=MagicMock(),
                repo_dir=repo_dir,
                release_version=release_version,
                repository_version_file_path=repository_version_file_path,
                repository_branch=repository_branch,
                version_operation=version_operation,
                prerelease_suffix=prerelease_suffix,
                next_version_callback=dev_cycle_commit_callback,
            )
        return _examinee

    def test_validation(self, examinee, tmp_path):
        # create temporary files in the provided directory
        temporary_callback_file = tmp_path.joinpath('callback_script')
        temporary_callback_file.touch()
        examinee(
            dev_cycle_commit_callback='callback_script',
        ).validate()

    def test_validation_fail_on_missing_dev_cycle_callback_script(self, examinee, tmp_path):
        with pytest.raises(ValueError):
            # pass non-existing relative file-name
            examinee(dev_cycle_commit_callback='no_such_file').validate()

    def test_validation_fail_on_missing_version_file(self, examinee, tmp_path):
        with pytest.raises(ValueError):
            examinee(repository_version_file_path='no_such_file').validate()

    def test_validation_fail_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()


class TestGitHubReleaseStep(object):
    @pytest.fixture()
    def examinee(self, tmp_path):
        # prepare test component descriptor file and fill it with test content
        component_descriptor_file = tmp_path.joinpath('test_descriptor').resolve()
        component_descriptor_file.write_text('component descriptor test content')

        def _examinee(
            githubrepobranch=GitHubRepoBranch(
                github_config='test_config',
                repo_owner='test_owner',
                repo_name='test_name',
                branch='master',
            ),
            repo_dir=str(tmp_path),
            release_version='1.0.0',
            tag_helper_return_value=False,
            component_descriptor_file_path=str(component_descriptor_file),
        ):
            # Create a github_helper mock that always reports a tag as existing/not existing,
            # depending on the passed value
            github_helper_mock = MagicMock(spec=GitHubRepositoryHelper)
            return concourse.steps.release.GitHubReleaseStep(
                github_helper=github_helper_mock,
                githubrepobranch=githubrepobranch,
                repo_dir=repo_dir,
                release_version=release_version,
                component_descriptor_file_path=component_descriptor_file_path,
            )
        return _examinee

    def test_validation(self, examinee):
        examinee().validate()

    def test_validation_fails_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()

    def test_validation_fails_on_missing_component_descriptor(self, examinee, tmp_path):
        test_path = tmp_path.joinpath('no', 'such', 'dir')
        with pytest.raises(ValueError):
            examinee(component_descriptor_file_path=str(test_path)).validate()

    def test_validation_fails_on_empty_component_descriptor(self, examinee, tmp_path):
        test_path = tmp_path.joinpath('empty_component_descriptor')
        test_path.touch()
        with pytest.raises(ValueError):
            examinee(component_descriptor_file_path=str(test_path)).validate()


class TestPublishReleaseNotesStep(object):
    @pytest.fixture()
    def examinee(self, tmp_path):
        def _examinee(
            github_helper=MagicMock(),
            githubrepobranch=GitHubRepoBranch(
                github_config='test_config',
                repo_owner='test_owner',
                repo_name='test_name',
                branch='master',
            ),
            repo_dir=str(tmp_path),
            release_version='1.0.0',
        ):
            return concourse.steps.release.PublishReleaseNotesStep(
                github_helper=github_helper,
                githubrepobranch=githubrepobranch,
                repo_dir=repo_dir,
                release_version=release_version,
            )
        return _examinee

    def test_validation(self, examinee):
        examinee().validate()

    def test_validation_fail_on_nonexistent_repo_dir(self, examinee, tmp_path):
        # create filepath not backed by an existing directory in the pytest tempdir
        test_dir = tmp_path.joinpath('no', 'such', 'dir')
        with pytest.raises(ValueError):
            examinee(repo_dir=str(test_dir)).validate()

    def test_validation_fails_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()


class TestTryCleanupDraftReleaseStep(object):
    @pytest.fixture()
    def examinee(self):
        def _examinee(
            github_helper=MagicMock(),
        ):
            return concourse.steps.release.TryCleanupDraftReleasesStep(
                github_helper=github_helper,
            )
        return _examinee

    def test_validation(self, examinee):
        examinee().validate()


class TestSlackReleaseStep(object):
    @pytest.fixture()
    def examinee(self):
        def _examinee(
            slack_cfg_name='test_config',
            slack_channel='test_channel',
            githubrepobranch=GitHubRepoBranch(
                github_config='test_config',
                repo_owner='test_owner',
                repo_name='test_name',
                branch='master',
            ),
            release_notes=ReleaseNotes(None),
            release_version='1.0.0',
        ):
            return concourse.steps.release.PostSlackReleaseStep(
                slack_cfg_name=slack_cfg_name,
                slack_channel=slack_channel,
                githubrepobranch=githubrepobranch,
                release_notes=release_notes,
                release_version=release_version,
            )
        return _examinee

    def test_validation(self, examinee):
        examinee().validate()

    def test_validation_fails_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()
