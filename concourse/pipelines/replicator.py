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
import sys

from copy import deepcopy
import itertools

import argparse
import mako.template

from util import (
    SimpleNamespaceDict, fail, ensure_directory_exists, ensure_file_exists, info, is_yaml_file, merge_dicts
)
from github.util import branches

from concourse.pipelines.factory import DefinitionFactory, RawPipelineDefinitionDescriptor
from concourse.pipelines.enumerator import PipelineEnumerator

from concourse import client
from model import ConcourseTeamCredentials, ConcourseConfig


def generate_pipelines(
        definitions_root_dir,
        job_mapping,
        template_path,
        template_include_dir,
        config_set: 'ConfigurationSet'
    ):
    enumerator = PipelineEnumerator(
        base_dir=definitions_root_dir,
        cfg_set=config_set,
    )

    pipeline_definitions = itertools.chain(*enumerator.enumerate_pipeline_definitions(job_mapping))

    github_cfg = config_set.github()


    for pipeline_definition in pipeline_definitions:
        rendering_results = render_pipelines(
            pipeline_definition=pipeline_definition,
            config_set=config_set,
            template_path=template_path,
            template_include_dir=template_include_dir
        )
        for rendered_pipeline, instance_definition, pipeline_args in rendering_results:
            yield (rendered_pipeline, instance_definition, pipeline_args)


def deploy_pipeline(
        pipeline_definition: dict,
        pipeline_name: str,
        concourse_cfg: ConcourseConfig,
        team_credentials: ConcourseTeamCredentials,
        unpause_pipeline: bool=True,
        expose_pipeline: bool=True,
    ):
    api = client.ConcourseApi(
        base_url=concourse_cfg.external_url(),
        team_name=team_credentials.teamname(),
    )
    api.login(
        team_credentials.teamname(),
        team_credentials.username(),
        team_credentials.passwd(),
    )
    api.set_pipeline(name=pipeline_name, pipeline_definition=pipeline_definition)
    if unpause_pipeline:
        api.unpause_pipeline(pipeline_name=pipeline_name)
    if expose_pipeline:
        api.expose_pipeline(pipeline_name=pipeline_name)


def find_template_file(template_name:str, template_path:[str]):
    # TODO: do not hard-code file name extension
    template_file_name = template_name + '.yaml'
    for path in template_path:
        for dirpath, _, filenames in os.walk(path):
            if template_file_name in filenames:
                return os.path.join(dirpath, template_file_name)
    fail(
        'could not find template {t}, tried in {p}'.format(
            t=str(template_name),
            p=','.join(map(str, template_path))
        )
    )


def render_pipelines(
    pipeline_definition: RawPipelineDefinitionDescriptor,
    config_set: 'ConfigurationSet',
    template_path,
    template_include_dir=None
):
    template_name = pipeline_definition.template
    template_file = find_template_file(template_name, template_path)

    if template_include_dir:
        template_include_dir = os.path.abspath(template_include_dir)
        from mako.lookup import TemplateLookup
        lookup = TemplateLookup([template_include_dir])
        # hacky: add (hard-coded) lib directory (in cc-pipelines) to sys.path
        import sys
        sys.path.append(os.path.join(template_include_dir, 'lib'))

    factory = DefinitionFactory(raw_definition_descriptor=pipeline_definition)
    pipeline_metadata = SimpleNamespaceDict()
    pipeline_metadata.definition = factory.create_pipeline_definition()
    pipeline_metadata.name = pipeline_definition.name
    generated_model = pipeline_metadata.definition

    # determine pipeline name (if there is main-repo, append the configured branch name)
    for variant in pipeline_metadata.definition.variants():
        # hack: take the first "main_repository" we find
        if not variant.has_main_repository():
            continue
        main_repo = variant.main_repository()
        pipeline_metadata.pipeline_name = '-'.join([pipeline_definition.name, main_repo.branch()])
        break
    else:
        # fallback in case no main_repository was found
        pipeline_metadata.pipeline_name = pipeline_definition.name

    t = mako.template.Template(filename=template_file, lookup=lookup)
    yield (
            t.render(
                instance_args=generated_model,
                config_set=config_set,
                pipeline=pipeline_metadata
                ),
            generated_model,
            pipeline_metadata
    )


def replicate_pipelines(
    cfg_set,
    concourse_cfg,
    job_mapping,
    definitions_root_dir,
    template_path,
    template_include_dir,
    unpause_pipelines: bool=True,
    expose_pipelines: bool=True,
):
    ensure_directory_exists(definitions_root_dir)
    team_name = job_mapping.team_name()
    team_credentials = concourse_cfg.team_credentials(team_name)

    pipeline_names = set()

    for rendered_pipeline, _, pipeline_metadata in generate_pipelines(
        definitions_root_dir=definitions_root_dir,
        job_mapping=job_mapping,
        template_path=template_path,
        template_include_dir=template_include_dir,
        config_set=cfg_set,
    ):
        pipeline_name = pipeline_metadata.pipeline_name
        pipeline_names.add(pipeline_name)
        info('deploying pipeline {p} to team {t}'.format(p=pipeline_name, t=team_name))
        deploy_pipeline(
            pipeline_definition=rendered_pipeline,
            pipeline_name=pipeline_name,
            concourse_cfg=concourse_cfg,
            team_credentials=team_credentials,
            unpause_pipeline=unpause_pipelines,
            expose_pipeline=expose_pipelines,
        )

    concourse_api = client.ConcourseApi(base_url=concourse_cfg.external_url(), team_name=team_name)
    concourse_api.login(
        team=team_name,
        username=team_credentials.username(),
        passwd=team_credentials.passwd()
    )

    # rm pipelines that were not contained in job_mapping
    pipelines_to_remove = set(concourse_api.pipelines()) - pipeline_names

    for pipeline_name in pipelines_to_remove:
        info('removing pipeline: {p}'.format(p=pipeline_name))
        concourse_api.delete_pipeline(pipeline_name)

    # order pipelines alphabetically
    pipeline_names = list(concourse_api.pipelines())
    pipeline_names.sort()
    concourse_api.order_pipelines(pipeline_names)

