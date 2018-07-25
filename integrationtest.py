# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import os

import yaml

from github.util import GitHubRepositoryHelper, _create_github_api_object
from util import fail, info
from model import ConfigFactory
from concourse.client import ConcourseApi
from concourse.pipelines.replicator import Renderer, ConcourseDeployer, DeployStatus
from concourse.pipelines.factory import RawPipelineDefinitionDescriptor
from concourse.pipelines.enumerator import (
    DefinitionDescriptorPreprocessor,
    DefinitionDescriptor,
    TemplateRetriever,
)

'''
Integration tests for concourse pipeline generator
'''

def deploy_and_run_smoketest_pipeline(
    config_dir: str,
    config_name: str,
    concourse_team_name: str,
    cc_pipelines_repo_dir: str,
    wait_for_job_execution: bool=False,
):
    config_factory = ConfigFactory.from_cfg_dir(cfg_dir=config_dir)
    config_set = config_factory.cfg_set(cfg_name=config_name)
    concourse_cfg = config_set.concourse()
    team_credentials = concourse_cfg.team_credentials(concourse_team_name)

    # as this is an integration test, hard-code assumptions about the layout of
    # our pipelines repository
    calcdir = lambda path: os.path.join(cc_pipelines_repo_dir, path)

    template_path = calcdir('templates')
    template_include_dir = cc_pipelines_repo_dir
    pipeline_name = 'cc-smoketest'
    job_name = 'cc-smoketest-master-head-update-job'

    # retrieve pipeline-definition from github at hardcoded location
    github_cfg = config_set.github()
    helper = GitHubRepositoryHelper(
      github_cfg=github_cfg,
      owner='kubernetes',
      name='cc-smoketest',
    )
    pipeline_definition = yaml.load(
        helper.retrieve_text_file_contents(
            file_path='.ci/smoketest-pipeline.yaml',
        ),
    )

    definition_descriptor = DefinitionDescriptor(
        pipeline_name=pipeline_name,
        pipeline_definition=pipeline_definition[pipeline_name],
        template_name=pipeline_definition[pipeline_name]['template'],
        main_repo={'path': 'kubernetes/cc-smoketest', 'branch': 'master'},
        concourse_target_cfg=concourse_cfg,
        concourse_target_team=concourse_team_name,
    )

    preprocessor = DefinitionDescriptorPreprocessor()
    template_retriever = TemplateRetriever(template_path=template_path)
    renderer = Renderer(
        template_retriever=template_retriever,
        template_include_dir=template_include_dir,
        cfg_set=config_set,
    )
    deployer = ConcourseDeployer(
        unpause_pipelines=True,
        expose_pipelines=True
    )

    definition_descriptor = preprocessor.process_definition_descriptor(definition_descriptor)
    rendering_result = renderer.render(definition_descriptor)

    deployment_result = deployer.deploy(rendering_result.definition_descriptor)

    if not deployment_result.deploy_status & DeployStatus.SUCCEEDED:
        fail('deployment failed')

    # skip triggering for now
    return

    api = ConcourseApi(base_url=concourse_cfg.external_url(), team_name=concourse_team_name)
    api.login(
        team=team_credentials.teamname(),
        username=team_credentials.username(),
        passwd=team_credentials.passwd()
    )

    # trigger an execution and wait for it to finish
    info('triggering smoketest job {jn}'.format(jn=job_name))
    api.trigger_build(deployment_result.definition_descriptor.pipeline_name, job_name)

    if not wait_for_job_execution:
        info('will not wait for job-execution to finish (--wait-for-job-execution not set)')
        return

    # wait for the job to finish (currently we expect it to succeed)
    # todo: evaluate whether its structure meets our spec

    builds = api.job_builds(pipeline_name, job_name)
    if not builds or len(builds) < 1:
        fail('no builds were found (expected at least one!)')

    last_build = builds[-1] # please let this be ours

    # now wait for it to finish
    build_event_handler = api.build_events(last_build.id())
    build_event_handler.process_events()

    info('it seems as if the job finished sucessfully; life is good :-)')


