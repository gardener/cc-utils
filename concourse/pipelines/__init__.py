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

# enable toplevel imports
import os
import sys
own_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(own_dir, os.path.pardir))

from copy import deepcopy
import itertools

import argparse
import mako.template

from util import (
    SimpleNamespaceDict, parse_yaml_file, fail, ensure_directory_exists, ensure_file_exists, info, is_yaml_file, merge_dicts
)
from githubutil import branches

from concourse.pipelines.factory import DefinitionFactory

from concourse import client
from model import ConcourseTeamCredentials, ConcourseConfig


def enumerate_pipeline_definitions(directories):
    for directory in directories:
        # for now, hard-code mandatory .repository_mapping
        repo_mapping = parse_yaml_file(os.path.join(directory, '.repository_mapping'))
        repo_definition_mapping = {repo_path: list() for repo_path in repo_mapping.keys()}

        for repo_path, definition_files in repo_mapping.items():
            for definition_file_path in definition_files:
                abs_file = os.path.abspath(os.path.join(directory, definition_file_path))
                pipeline_raw_definition = parse_yaml_file(abs_file, as_snd=False)
                repo_definition_mapping[repo_path].append(pipeline_raw_definition)

        yield repo_definition_mapping.items()


def generate_pipelines(
        definition_directories,
        template_path,
        template_include_dir,
        config_set: 'ConfigurationSet'
    ):
    repo_pipeline_definition_mappings = itertools.chain(
            *enumerate_pipeline_definitions(definition_directories)
    )

    # inject base repo definition, multiply by branches
    pipeline_definitions = []
    github_cfg = config_set.github()


    for repo_path, pipeline_defs in repo_pipeline_definition_mappings:
        # determine branches
        org, repo_name = repo_path.split('/')
        branch_names = branches(github_cfg=github_cfg, repo_owner=org, repo_name=repo_name)

        for pd in pipeline_defs:
            for branch_name in branch_names:
                pd = deepcopy(pd)
                main_repo_raw = {'path': repo_path, 'branch': branch_name}
                for pipeline_name, pipeline_args in pd.items():
                    # todo: mv this into pipeline-definition-factory
                    base_definition = pipeline_args.get('base_definition', {})
                    if base_definition.get('repo'):
                        merged_main_repo = merge_dicts(base_definition['repo'], main_repo_raw)
                        base_definition['repo'] = merged_main_repo
                    else:
                        base_definition['repo'] = main_repo_raw
                    # create "old" structure as a quick temporary hack
                    pipeline_definition = {
                        'pipeline': pipeline_args,
                    }
                    pipeline_definition['name'] = pipeline_name
                    pipeline_definitions.append(SimpleNamespaceDict(pipeline_definition))

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
    api.unpause_pipeline(pipeline_name=pipeline_name)


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
    pipeline_definition,
    config_set: 'ConfigurationSet',
    template_path,
    template_include_dir=None
):
    template_name = pipeline_definition.pipeline.template
    template_file = find_template_file(template_name, template_path)

    if template_include_dir:
        template_include_dir = os.path.abspath(template_include_dir)
        from mako.lookup import TemplateLookup
        lookup = TemplateLookup([template_include_dir])
        # hacky: add (hard-coded) lib directory (in cc-pipelines) to sys.path
        import sys
        sys.path.append(os.path.join(template_include_dir, 'lib'))

    factory = DefinitionFactory(raw_dict=dict(pipeline_definition.pipeline))
    pipeline_metadata = SimpleNamespaceDict()
    pipeline_metadata.definition = factory.create_pipeline_args()
    pipeline_metadata.name = pipeline_definition.name

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
                instance_args=pipeline_definition.pipeline,
                config_set=config_set,
                pipeline=pipeline_metadata
                ),
            pipeline_definition.pipeline,
            pipeline_metadata
    )


def replicate_pipelines(
    cfg_set,
    concourse_cfg,
    job_mapping,
    definitions_root_dir,
    template_path,
    template_include_dir,
):
    ensure_directory_exists(definitions_root_dir)
    definition_dirs = [
        ensure_directory_exists(os.path.abspath(os.path.join(definitions_root_dir, dd)))
        for dd in job_mapping.definition_dirs()
    ]
    team_name = job_mapping.team_name()
    team_credentials = concourse_cfg.team_credentials(team_name)

    pipeline_names = set()

    for rendered_pipeline, _, pipeline_metadata in generate_pipelines(
        definition_directories=definition_dirs,
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
    pipeline_names = concourse_api.pipelines()
    pipeline_names.sort()
    concourse_api.order_pipelines(pipeline_names)

