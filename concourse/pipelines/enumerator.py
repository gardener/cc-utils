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
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import chain
from urllib.parse import urlparse
import functools
import yaml

from github3.exceptions import NotFoundError

from util import (
    parse_yaml_file,
    merge_dicts,
    info,
    verbose,
    ensure_directory_exists,
    not_empty,
    not_none,
)
from github.util import _create_github_api_object
from model import JobMapping
from concourse.pipelines.factory import RawPipelineDefinitionDescriptor

class DefinitionDescriptorPreprocessor(object):
    def process_definition_descriptor(self, descriptor):
        self._add_branch_to_pipeline_name(descriptor)
        return self._inject_main_repo(descriptor)

    def _add_branch_to_pipeline_name(self, descriptor):
        descriptor.pipeline_name = '{n}-{b}'.format(
            n=descriptor.pipeline_name,
            b=descriptor.main_repo.get('branch'),
        )
        return descriptor

    def _inject_main_repo(self, descriptor):
        descriptor.override_definitions.append({
            'base_definition':
            {
                'repo': descriptor.main_repo
            }
        })

        return descriptor

class DefinitionEnumerator(object):
    def enumerate_definition_descriptors(self):
        raise NotImplementedError('subclasses must override')

    def _wrap_into_descriptors(
        self,
        repo_path,
        repo_hostname,
        branch,
        raw_definitions
        ) -> 'DefinitionDescriptor':
        for name, definition in raw_definitions.items():
            pipeline_definition = deepcopy(definition)
            yield DefinitionDescriptor(
                pipeline_name=name,
                pipeline_definition=pipeline_definition,
                template_name=pipeline_definition['template'],
                main_repo={'path': repo_path, 'branch': branch, 'hostname': repo_hostname},
                concourse_target_cfg=self.cfg_set.concourse(),
                concourse_target_team=self.job_mapping.team_name(),
                override_definitions=[{},],
            )



class MappingfileDefinitionEnumerator(DefinitionEnumerator):
    def __init__(self, base_dir, job_mapping, cfg_set):
        self.base_dir = ensure_directory_exists(base_dir)
        self.job_mapping = job_mapping
        self.cfg_set = cfg_set

    def enumerate_definition_descriptors(self):
        # handle definition directories (legacy-case)
        info('scanning legacy mappings')
        for repo_path, pd in self._enumerate_pipeline_definitions(
                [os.path.join(self.base_dir, d) for d in self.job_mapping.definition_dirs()]
        ):
            for definitions in pd:
                for name, definition in definitions.items():
                    info('from mapping: ' + name)
                    yield from self._wrap_into_descriptors(
                        repo_path=repo_path,
                        # XXX un-hardcode
                        repo_hostname='github.com',
                        branch='master',
                        raw_definition=definitions
                    )

    def _enumerate_pipeline_definitions(self, directories):
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


class GithubOrganisationDefinitionEnumerator(DefinitionEnumerator):
    def __init__(self, job_mapping, cfg_set):
        self.job_mapping = not_none(job_mapping)
        self.cfg_set = not_none(cfg_set)

    def enumerate_definition_descriptors(self):
        executor = ThreadPoolExecutor(max_workers=8)

        # scan github repositories
        for github_org_cfg in self.job_mapping.github_organisations():
            github_cfg = self.cfg_set.github(github_org_cfg.github_cfg_name())
            github_org_name = github_org_cfg.org_name()
            info('scanning github organisation {gho}'.format(gho=github_org_name))

            branch_filter = lambda b: b == 'master'
            github_api = _create_github_api_object(github_cfg)
            github_org = github_api.organization(github_org_name)

            scan_repository_for_definitions = functools.partial(
                self._scan_repository_for_definitions,
                github_cfg=github_cfg,
                org_name=github_org_name,
                branch_filter=branch_filter,
            )

            for definition_descriptors in executor.map(
                scan_repository_for_definitions,
                github_org.repositories(),
            ):
                yield from definition_descriptors


    def _scan_repository_for_definitions(
        self,
        repository,
        github_cfg,
        org_name,
        branch_filter
    ) -> RawPipelineDefinitionDescriptor:
        # always use default branch for now
        try:
            default_branch = repository.default_branch
        except:
            default_branch = 'master'

        for branch_name in [default_branch]:
            try:
                definitions = repository.file_contents(
                    path='.ci/pipeline_definitions',
                    ref=branch_name
                )
            except NotFoundError:
                continue # no pipeline definition for this branch

            repo_hostname = urlparse(github_cfg.http_url()).hostname

            verbose('from repo: ' + repository.name + ':' + branch_name)
            definitions = yaml.load(definitions.decoded.decode('utf-8'))
            yield from self._wrap_into_descriptors(
                repo_path='/'.join([org_name, repository.name]),
                repo_hostname=repo_hostname,
                branch=branch_name,
                raw_definitions=definitions
            )


class DefinitionDescriptor(object):
    def __init__(
        self,
        pipeline_name,
        pipeline_definition,
        template_name,
        main_repo,
        concourse_target_cfg,
        concourse_target_team,
        override_definitions=[{},]
    ):
        self.pipeline_name = not_empty(pipeline_name)
        self.pipeline_definition = not_none(pipeline_definition)
        self.template_name = not_empty(template_name)
        self.main_repo = not_none(main_repo)
        self.concourse_target_cfg = not_none(concourse_target_cfg)
        self.concourse_target_team = not_none(concourse_target_team)
        self.override_definitions = not_none(override_definitions)

    def concourse_target(self):
        return (self.concourse_target_cfg, self.concourse_target_team)

    def concourse_target_key(self):
        return '{n}:{t}'.format(
            n=self.concourse_target_cfg.name(),
            t=self.concourse_target_team
        )


class TemplateRetriever(object):
    '''
    Provides mako templates by name. Templates are cached.
    '''
    def __init__(self, template_path):
        if type(template_path) == str:
            self.template_path = (template_path,)
        else:
            self.template_path = template_path

    @functools.lru_cache()
    def template_file(self, template_name):
        # TODO: do not hard-code file name extension
        template_file_name = template_name + '.yaml'
        for path in self.template_path:
            for dirpath, _, filenames in os.walk(path):
                if template_file_name in filenames:
                    return os.path.join(dirpath, template_file_name)
        fail(
            'could not find template {t}, tried in {p}'.format(
                t=str(template_name),
                p=','.join(map(str, template_path))
            )
        )

    @functools.lru_cache()
    def template_contents(self, template_name):
        with open(self.template_file(template_name=template_name)) as f:
            return f.read()

