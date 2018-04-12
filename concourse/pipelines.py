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

import argparse
import mako.template

from util import (
    SimpleNamespaceDict, parse_yaml_file, fail, ensure_directory_exists, ensure_file_exists, info, is_yaml_file
)

from concourse import client as client
from model import ConcourseTeamCredentials, ConcourseConfig

def enumerate_pipeline_definitions(directories):
    for directory in directories:
        for path, _, files in os.walk(directory):
            abs_files = map(lambda f: os.path.join(path, f), files)
            for f in filter(is_yaml_file, abs_files):
                yield parse_yaml_file(f)


def generate_pipelines(
        definition_directories,
        template_path,
        template_include_dir,
        config_set: 'ConfigurationSet'
    ):
    pipeline_definitions = enumerate_pipeline_definitions(definition_directories)

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
    #TODO: find a better way to pass pipeline name to template
    pipeline_args = SimpleNamespaceDict({'name': pipeline_definition.pipeline.name})

    instance_definition = pipeline_definition.pipeline
    template_name = instance_definition.template
    template_file = find_template_file(template_name, template_path)

    if template_include_dir:
        template_include_dir = os.path.abspath(template_include_dir)
        from mako.lookup import TemplateLookup
        lookup = TemplateLookup([template_include_dir])
        # hacky: add (hard-coded) lib directory (in cc-pipelines) to sys.path
        import sys
        sys.path.append(os.path.join(template_include_dir, 'lib'))

    t = mako.template.Template(filename=template_file, lookup=lookup)
    yield (
            t.render(
                instance_args=instance_definition.template_args,
                config_set=config_set,
                pipeline=pipeline_args
                ),
            instance_definition,
            pipeline_args
    )

