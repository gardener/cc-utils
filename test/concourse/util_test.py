# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import concourse.util as examinee
import concourse.model.traits.meta
import concourse.steps.meta


def test_static_pipeline_metadata(monkeypatch, tmp_path):
    # fake running on ci
    monkeypatch.setenv('CC_ROOT_DIR', tmp_path)
    meta_dir_path = tmp_path / concourse.model.traits.meta.META_INFO_DIR_NAME
    meta_dir_path.mkdir()
    uuid_file_path = meta_dir_path / concourse.steps.meta.jobmetadata_filename

    TEST_CONFIG_SET_NAME = 'made-up_config_name'
    TEST_CONCOURSE_TEAM_NAME = 'made-up_concourse_team'
    TEST_PIPELINE_NAME = 'made-up_pipeline_name'
    TEST_JOB_NAME = 'made-up_job_name'
    TEST_BUILD_UUID = 'made-up-UUID'

    with open(uuid_file_path, 'w') as f:
        json.dump({'uuid': TEST_BUILD_UUID}, f)

    test_metadata = examinee.PipelineMetaData(
        pipeline_name=TEST_PIPELINE_NAME,
        job_name=TEST_JOB_NAME,
        current_config_set_name=TEST_CONFIG_SET_NAME,
        team_name=TEST_CONCOURSE_TEAM_NAME,
        build_uuid=TEST_BUILD_UUID,
    )

    monkeypatch.setenv('CONCOURSE_CURRENT_CFG', TEST_CONFIG_SET_NAME)
    monkeypatch.setenv('CONCOURSE_CURRENT_TEAM', TEST_CONCOURSE_TEAM_NAME)
    monkeypatch.setenv('PIPELINE_NAME', TEST_PIPELINE_NAME)
    monkeypatch.setenv('BUILD_JOB_NAME', TEST_JOB_NAME)

    pipeline_metadata = examinee.get_pipeline_metadata()

    assert pipeline_metadata == test_metadata
