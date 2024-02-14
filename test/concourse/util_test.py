# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import concourse.util as examinee


def test_static_pipeline_metadata(monkeypatch):
    # fake running on ci
    monkeypatch.setenv('CC_ROOT_DIR', 'made/up/dir')

    TEST_CONFIG_SET_NAME = 'made-up_config_name'
    TEST_CONCOURSE_TEAM_NAME = 'made-up_concourse_team'
    TEST_PIPELINE_NAME = 'made-up_pipeline_name'
    TEST_JOB_NAME = 'made-up_job_name'

    test_metadata = examinee.PipelineMetaData(
        pipeline_name=TEST_PIPELINE_NAME,
        job_name=TEST_JOB_NAME,
        current_config_set_name=TEST_CONFIG_SET_NAME,
        team_name=TEST_CONCOURSE_TEAM_NAME,
    )

    monkeypatch.setenv('CONCOURSE_CURRENT_CFG', TEST_CONFIG_SET_NAME)
    monkeypatch.setenv('CONCOURSE_CURRENT_TEAM', TEST_CONCOURSE_TEAM_NAME)
    monkeypatch.setenv('PIPELINE_NAME', TEST_PIPELINE_NAME)
    monkeypatch.setenv('BUILD_JOB_NAME', TEST_JOB_NAME)

    pipeline_metadata = examinee.get_pipeline_metadata()

    assert pipeline_metadata == test_metadata
