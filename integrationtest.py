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

from util import fail, info, parse_yaml_file
from model import ConfigFactory
from concourse.client import ConcourseApi
from concourse.pipelines import render_pipelines, deploy_pipeline
from concourse.pipelines.factory import RawPipelineDefinitionDescriptor

'''
Integration tests for concourse pipeline generator
'''

def deploy_and_run_smoketest_pipeline(
    config_dir: str,
    config_name: str,
    concourse_team_name: str,
    cc_pipelines_repo_dir: str
):
    config_factory = ConfigFactory.from_cfg_dir(cfg_dir=config_dir)
    config_set = config_factory.cfg_set(cfg_name=config_name)
    concourse_cfg = config_set.concourse()
    team_credentials = concourse_cfg.team_credentials(concourse_team_name)

    # as this is an integration test, hard-code assumptions about the layout of
    # our pipelines repository
    calcdir = lambda path: os.path.join(cc_pipelines_repo_dir, path)

    pipeline_definition_file = calcdir('definitions/test/cc-smoketest.yaml')
    template_path = calcdir('templates')
    template_include_dir = cc_pipelines_repo_dir
    pipeline_name = 'cc-smoketest'
    job_name = 'cc-smoketest-master-head-update-job'

    pipeline_definition = parse_yaml_file(pipeline_definition_file, as_snd=False)

    pipeline_descriptor = RawPipelineDefinitionDescriptor(
        name=pipeline_name,
        base_definition=pipeline_definition[pipeline_name]['base_definition'],
        variants=pipeline_definition[pipeline_name]['variants'],
        template=pipeline_definition[pipeline_name]['template'],
    )

    rendered_pipelines = list(
        render_pipelines(
            pipeline_definition=pipeline_descriptor,
            config_set=config_set,
            template_path=[template_path],
            template_include_dir=template_include_dir,
        )
    )
    if len(rendered_pipelines) == 0:
        fail("smoke-test pipeline definition not found")
    if len(rendered_pipelines) > 1:
        fail("expected exactly one smoketest pipeline-definition, got {n}".format(n=len(rendered_pipelines)))
    pipeline_definition, _, _ = rendered_pipelines[0]

    deploy_pipeline(
      pipeline_definition=pipeline_definition,
      pipeline_name=pipeline_name,
      concourse_cfg=concourse_cfg,
      team_credentials=team_credentials,
    )

    api = ConcourseApi(base_url=concourse_cfg.external_url(), team_name=concourse_team_name)
    api.login(
        team=team_credentials.teamname(),
        username=team_credentials.username(),
        passwd=team_credentials.passwd()
    )

    # trigger an execution and wait for it to finish
    info('triggering smoketest job {jn}'.format(jn=job_name))
    api.trigger_build(pipeline_name, job_name)

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


