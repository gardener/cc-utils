# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from github.util import (
    GitHubRepositoryHelper,
    GitHubRepoBranch,
)
from ci.util import fail, info
from model import ConfigFactory
from concourse.replicator import Renderer, ConcourseDeployer, DeployStatus
from concourse.enumerator import (
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
    cc_utils_repo_dir: str,
    wait_for_job_execution: bool=False,
):
    config_factory = ConfigFactory.from_cfg_dir(cfg_dir=config_dir)
    config_set = config_factory.cfg_set(cfg_name=config_name)
    concourse_cfg = config_set.concourse()

    # as this is an integration test, hard-code assumptions about the layout of
    # our pipelines repository
    template_path = os.path.join(cc_utils_repo_dir, 'concourse', 'templates')
    template_include_dir = os.path.join(cc_utils_repo_dir, 'concourse')
    pipeline_name = 'cc-smoketest'

    # retrieve pipeline-definition from github at hardcoded location
    github_cfg = config_set.github()

    githubrepobranch = GitHubRepoBranch(
        github_config=github_cfg,
        repo_owner='kubernetes',
        repo_name='cc-smoketest',
        branch='master',
    )

    helper = GitHubRepositoryHelper.from_githubrepobranch(
      githubrepobranch=githubrepobranch,
    )
    pipeline_definition = yaml.load(
        helper.retrieve_text_file_contents(
            file_path='.ci/smoketest-pipeline.yaml',
        ),
        Loader=yaml.SafeLoader,
    )

    definition_descriptor = DefinitionDescriptor(
        pipeline_name=pipeline_name,
        pipeline_definition=pipeline_definition[pipeline_name],
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

    info('deploying pipeline')
    deployment_result = deployer.deploy(rendering_result.definition_descriptor)

    if not deployment_result.deploy_status & DeployStatus.SUCCEEDED:
        fail('deployment failed')
