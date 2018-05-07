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
from copy import deepcopy
from itertools import chain
import yaml
from github3.exceptions import NotFoundError

from util import (
    parse_yaml_file,
    merge_dicts,
    info,
)
from githubutil import _create_github_api_object
from model import JobMapping
from concourse.pipelines.factory import RawPipelineDefinitionDescriptor


class PipelineEnumerator(object):
    def __init__(self, base_dir, cfg_set):
        self.base_dir = base_dir
        self.cfg_set = cfg_set

    def enumerate_pipeline_definitions(self, job_mapping: JobMapping):
        # handle definition directories (legacy-case)
        info('scanning legacy mappings')
        for repo_path, pd in enumerate_pipeline_definitions(
                [os.path.join(self.base_dir, d) for d in job_mapping.definition_dirs()]
        ):
            for definitions in pd:
                for name, definition in definitions.items():
                    info('from mapping: ' + name)
                    yield self._preprocess_and_wrap_into_descriptors(repo_path, 'master', definitions)
        info('scanning repositories')
        # scan github repositories
        for github_org_cfg in job_mapping.github_organisations():
            github_cfg = self.cfg_set.github(github_org_cfg.github_cfg_name())
            github_org_name = github_org_cfg.org_name()

            branch_filter = lambda b: b == 'master'
            github_api = _create_github_api_object(github_cfg)
            github_org = github_api.organization(github_org_name)

            for repository in github_org.repositories():
                yield self._scan_repository_for_definitions(
                    github_org_name,
                    repository,
                    branch_filter
                )

    def _scan_repository_for_definitions(self, org_name, repository, branch_filter):
        for branch_name in filter(branch_filter, map(lambda b: b.name, repository.branches())):
            try:
                definitions = repository.file_contents(
                    path='.ci/pipeline_definitions',
                    ref=branch_name
                )
            except NotFoundError:
                continue # no pipeline definition for this branch

            info('from repo: ' + repository.name + ':' + branch_name)
            definitions = yaml.load(definitions.decoded.decode('utf-8'))
            yield from self._preprocess_and_wrap_into_descriptors(
                repo_path='/'.join([org_name, repository.name]),
                branch=branch_name,
                raw_definitions=definitions
            )


    def _preprocess_and_wrap_into_descriptors(self, repo_path, branch, raw_definitions):
        for name, definition in raw_definitions.items():
            pipeline_definition = deepcopy(definition)
            base_definition = self._inject_main_repo(
                base_definition=definition.get('base_definition', {}),
                repo_path=repo_path,
                branch_name=branch,
            )
            yield RawPipelineDefinitionDescriptor(
                name=name, #'-'.join(name, branch),
                base_definition=base_definition,
                variants=definition['variants'],
                template=definition['template'],
            )

    def _inject_main_repo(self, base_definition, repo_path, branch_name):
        main_repo_raw = {'path': repo_path, 'branch': branch_name}

        if base_definition.get('repo'):
            merged_main_repo = merge_dicts(base_definition['repo'], main_repo_raw)
            base_definition['repo'] = merged_main_repo
        else:
            base_definition['repo'] = main_repo_raw

        return base_definition


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

        for repo_path, definitions in  repo_definition_mapping.items():
            yield (repo_path, definitions)
