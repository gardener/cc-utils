import dataclasses
import pytest
from unittest.mock import MagicMock
from github.util import GitHubRepositoryHelper, GitHubRepoBranch
import os
import yaml

import concourse.steps.release
from concourse.model.traits.release import (
    ReleaseCommitPublishingPolicy,
)
import gci.componentmodel as cm


class TestReleaseCommitStep:
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
                release_commit_callback_image_reference=None,
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


class NextDevCycleCommitStep:
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


class TestGitHubReleaseStep:
    @pytest.fixture()
    def examinee(self, tmp_path):
        # prepare test component descriptor file and fill it with test content

        component_descriptor_v2 = os.path.join(tmp_path, 'component_descriptor_v2')
        cd_v2 = cm.ComponentDescriptor(
            component=cm.Component(
                name='test.url/foo/bar',
                version='1.2.3',
                repositoryContexts=[],
                provider={
                    'name': 'some provider',
                },
                sources=[],
                componentReferences=[],
                resources=[],
            ),
            meta=cm.Metadata(),
        )
        with open(component_descriptor_v2, 'w') as f:
            yaml.dump(
                data=dataclasses.asdict(cd_v2),
                stream=f,
                Dumper=cm.EnumValueYamlDumper,
            )

        def _examinee(
            githubrepobranch=GitHubRepoBranch(
                github_config='test_config',
                repo_owner='test_owner',
                repo_name='test_name',
                branch='master',
            ),
            repo_dir=str(tmp_path),
            release_version='1.0.0',
            component_name='github.test/test/component',
            tag_helper_return_value=False,
        ):
            # Create a github_helper mock that always reports a tag as existing/not existing,
            # depending on the passed value
            github_helper_mock = MagicMock(spec=GitHubRepositoryHelper)
            return concourse.steps.release.GitHubReleaseStep(
                github_helper=github_helper_mock,
                githubrepobranch=githubrepobranch,
                repo_dir=repo_dir,
                release_version=release_version,
                component_name=component_name,
            )
        return _examinee

    def test_validation(self, examinee):
        examinee().validate()

    def test_validation_fails_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()


class TestTryCleanupDraftReleaseStep:
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


class TestSlackReleaseStep:
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
            release_notes_markdown="",
            release_version='1.0.0',
        ):
            return concourse.steps.release.PostSlackReleaseStep(
                slack_cfg_name=slack_cfg_name,
                slack_channel=slack_channel,
                githubrepobranch=githubrepobranch,
                release_notes_markdown=release_notes_markdown,
                release_version=release_version,
            )
        return _examinee

    def test_validation(self, examinee):
        examinee().validate()

    def test_validation_fails_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()
