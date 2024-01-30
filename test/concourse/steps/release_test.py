import pytest
from unittest.mock import MagicMock

import concourse.steps.release


class NextDevCycleCommitStep:
    @pytest.fixture()
    def examinee(self, tmp_path):
        # create required temporary file relative to the provided temporary directory
        temporary_version_file = tmp_path.joinpath('version_file')
        temporary_version_file.touch()

        def _examinee(
            repo_dir=str(tmp_path),
            release_version='1.0.0',
            version_path='version_file',
            repository_branch='master',
            version_operation='bump_minor',
            prerelease_suffix='dev',
            dev_cycle_commit_callback=None,
            ):
            return concourse.steps.release.NextDevCycleCommitStep(
                git_helper=MagicMock(),
                repo_dir=repo_dir,
                release_version=release_version,
                version_path=version_path,
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
            examinee(version_path='no_such_file').validate()

    def test_validation_fail_on_invalid_semver(self, examinee):
        with pytest.raises(ValueError):
            examinee(release_version='invalid_semver').validate()
