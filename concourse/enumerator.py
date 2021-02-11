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
import re

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import urlparse
import functools

import yaml

from github3.exceptions import NotFoundError

from ci.util import (
    LintingError,
    fail,
    info,
    lint_yaml,
    load_yaml,
    merge_dicts,
    not_empty,
    not_none,
    parse_yaml_file,
    warning,
    verbose,
)
from model.base import ModelBase, NamedModelElement
from concourse.factory import RawPipelineDefinitionDescriptor
import ccc.github
import concourse.paths


class JobMappingNotFoundError(ValueError):
    ''' Signals that no JobMapping was found for a GitHub repository when processing.
    '''
    pass


class DefinitionDescriptorPreprocessor:
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


class DefinitionEnumerator:
    def enumerate_definition_descriptors(self):
        raise NotImplementedError('subclasses must override')

    def _wrap_into_descriptors(
        self,
        repo_path,
        repo_hostname,
        branch,
        raw_definitions,
        override_definitions={},
        target_team: str=None,
    ) -> 'DefinitionDescriptor':
        if not target_team:
            target_team = self.job_mapping.team_name()

        for name, definition in raw_definitions.items():
            pipeline_definition = deepcopy(definition)
            yield DefinitionDescriptor(
                pipeline_name=name,
                pipeline_definition=pipeline_definition or {},
                main_repo={'path': repo_path, 'branch': branch, 'hostname': repo_hostname},
                concourse_target_cfg=self.cfg_set.concourse(),
                concourse_target_team=target_team,
                override_definitions=[override_definitions.get(name,{}),],
            )


class SimpleFileDefinitionEnumerator(DefinitionEnumerator):
    def __init__(self, definition_file, cfg_set, repo_path, repo_branch, repo_host='github.com'):
        self.definition_file = definition_file
        self.repo_path = repo_path
        self.repo_branch = repo_branch
        self.repo_host = repo_host
        self.cfg_set = cfg_set
        import model
        self.job_mapping = model.concourse.JobMapping(
            name='dummy',
            raw_dict={'concourse_target_team': 'dummy'},
        )

    def enumerate_definition_descriptors(self):
        info('enumerating explicitly specified definition file')

        try:
            definitions = parse_yaml_file(self.definition_file)
            yield from self._wrap_into_descriptors(
                repo_path=self.repo_path,
                repo_hostname=self.repo_host,
                branch=self.repo_branch,
                raw_definitions=definitions,
            )
        except BaseException as e:
            yield DefinitionDescriptor(
                pipeline_name='<invalid YAML>',
                pipeline_definition={},
                main_repo={
                    'path': self.repo_path,
                    'branch': self.repo_branch,
                    'hostname': self.repo_host,
                },
                concourse_target_cfg=self.cfg_set.concourse(),
                concourse_target_team=self.job_mapping.team_name(),
                override_definitions=(),
                exception=e,
            )


class BranchCfg(ModelBase):
    def _required_attributes(self):
        return {'cfgs'}

    def cfg_entries(self):
        return (
            BranchCfgEntry(name=name, raw_dict=raw_dict)
            for name, raw_dict in self.raw.get('cfgs').items()
        )

    def cfg_entry_for_branch(self, branch):
        matching_entries = [
            entry for entry in self.cfg_entries() if entry.branch_matches(branch)
        ]
        if not matching_entries:
            return None

        # merge entries
        effective_branch_cfg = {}

        # order by optional attribute 'index' (random order if omitted)
        def entry_with_idx(idx, entry):
            if entry.index():
                idx = int(entry.index())
            return (idx, entry)

        indexed_entries = [entry_with_idx(idx, entry) for idx, entry in enumerate(matching_entries)]

        for _, entry in sorted(indexed_entries, key=lambda i: i[0]):
            effective_branch_cfg = merge_dicts(effective_branch_cfg, entry.raw)

        return BranchCfgEntry(name='merged', raw_dict=effective_branch_cfg)


class BranchCfgEntry(NamedModelElement):
    def _optional_attributes(self):
        return {'index'}

    def index(self):
        return self.raw.get('index')

    def branches(self):
        return self.raw.get('branches')

    def branch_matches(self, branch):
        for b in self.branches():
            if re.fullmatch(b, branch):
                return True
        return False

    def override_definitions(self):
        return self.raw.get('inherit', {})

    def __repr__(self):
        return f'BranchCfgEntry: {self.raw}'


class GithubDefinitionEnumeratorBase(DefinitionEnumerator):
    def _branch_cfg_or_none(
        self,
        repository,
    ):
        try:
            branch_cfg = repository.file_contents(
                path='branch.cfg',
                ref='refs/meta/ci',
            ).decoded.decode('utf-8')
            info(f'Linting branch cfg for {repository}')
            lint_yaml(branch_cfg)
        except NotFoundError:
            return None # no branch cfg present

        return BranchCfg(raw_dict=load_yaml(branch_cfg))

    def _determine_repository_branches(
        self,
        repository,
    ):
        try:
            branch_cfg = self._branch_cfg_or_none(repository=repository)
        except LintingError as e:
            # some linting errors (and possibly warnings) present. Print warning and continue
            warning(e)
            return
        if not branch_cfg:
            # fallback for components w/o branch_cfg: use default branch
            try:
                default_branch = repository.default_branch
            except Exception:
                default_branch = 'master'
            yield (default_branch, None)
            return

        for branch in repository.branches():
            cfg_entry = branch_cfg.cfg_entry_for_branch(branch.name)
            if cfg_entry:
                yield (branch.name, cfg_entry)

    def _scan_repository_for_definitions(
        self,
        repository,
        github_cfg,
        org_name,
        repository_filter: callable=None,
        target_team: str=None,
    ) -> RawPipelineDefinitionDescriptor:

        repo_hostname = urlparse(github_cfg.http_url()).hostname
        repo_path = f'{org_name}/{repository.name}'

        if not target_team:
            target_team = self.job_mapping.team_name()

        try:
            branches_and_cfg_entries = [
                i for i in self._determine_repository_branches(repository=repository)
            ]
        except yaml.scanner.ScannerError as e:
            yield DefinitionDescriptor(
                pipeline_name='<invalid YAML>',
                pipeline_definition=None,
                main_repo={'path': repo_path, 'branch': 'refs/meta/ci', 'hostname': repo_hostname},
                concourse_target_cfg=self.cfg_set.concourse(),
                concourse_target_team=target_team,
                override_definitions=(),
                exception=e,
            )
            return # nothing else to yield in case parsing the branch cfg failed

        for branch_name, cfg_entry in branches_and_cfg_entries:
            try:
                definitions = repository.file_contents(
                    path='.ci/pipeline_definitions',
                    ref=branch_name
                )
            except NotFoundError:
                continue # no pipeline definition for this branch

            override_definitions = cfg_entry.override_definitions() if cfg_entry else {}

            verbose('from repo: ' + repository.name + ':' + branch_name)
            try:
                decoded_definitions = definitions.decoded.decode('utf-8')
                info(f'Linting pipeline_definitions for {repository} on branch {branch_name}')
                lint_yaml(decoded_definitions)
                definitions = load_yaml(decoded_definitions)

            except BaseException as e:
                yield DefinitionDescriptor(
                    pipeline_name='<invalid YAML>',
                    pipeline_definition={},
                    main_repo={'path': repo_path, 'branch': branch_name, 'hostname': repo_hostname},
                    concourse_target_cfg=self.cfg_set.concourse(),
                    concourse_target_team=target_team,
                    override_definitions=(),
                    exception=e,
                )
                return # nothing else to yield in case parsing failed

            # handle inheritance
            definitions = merge_dicts(definitions, override_definitions)

            # hacky: only set from GithubRepositoryDefinitionEnumerator
            target_team = getattr(self, '_target_team', None)

            yield from self._wrap_into_descriptors(
                repo_path='/'.join([org_name, repository.name]),
                repo_hostname=repo_hostname,
                branch=branch_name,
                raw_definitions=definitions,
                override_definitions=override_definitions,
                target_team=target_team,
            )


class GithubRepositoryDefinitionEnumerator(GithubDefinitionEnumeratorBase):
    def __init__(
        self,
        repository_url:str,
        cfg_set,
        target_team: str=None,
    ):
        if not '://' in repository_url:
            repository_url = 'x://' + repository_url

        self._repository_url = urlparse(not_none(repository_url))
        self._repo_host = self._repository_url.hostname
        self.cfg_set = not_none(cfg_set)
        self._target_team = target_team
        concourse_cfg = cfg_set.concourse()
        job_mapping_set = cfg_set.job_mapping(concourse_cfg.job_mapping_cfg_name())

        org_name, repo_name = self._repository_url.path.lstrip('/').split('/')

        if target_team:
            return # if tgt-team is explicitly configured, we do not need to look it up

        for job_mapping in job_mapping_set.job_mappings().values():
            for org in job_mapping.github_organisations():
                if org.org_name() != org_name:
                    continue
                if not org.repository_matches(repo_name):
                    continue
                github_cfg = cfg_set.github(org.github_cfg_name())
                if github_cfg.matches_hostname(self._repo_host):
                    self.job_mapping = job_mapping
                    return
        else:
            raise JobMappingNotFoundError(f'could not find matching job-mapping for org {org}')

    def enumerate_definition_descriptors(self):
        github_cfg = ccc.github.github_cfg_for_hostname(
            cfg_factory=self.cfg_set,
            host_name=self._repository_url.hostname,
        )
        github_api = ccc.github.github_api(github_cfg=github_cfg)
        github_org, github_repo = self._repository_url.path.lstrip('/').split('/')
        repository = github_api.repository(github_org, github_repo)

        yield from self._scan_repository_for_definitions(
            repository=repository,
            github_cfg=github_cfg,
            org_name=github_org,
            target_team=self._target_team,
        )


class GithubOrganisationDefinitionEnumerator(GithubDefinitionEnumeratorBase):
    def __init__(
        self,
        job_mapping,
        cfg_set,
        repository_filter: callable=None,
    ):
        self.job_mapping = not_none(job_mapping)
        self.cfg_set = not_none(cfg_set)
        self.repository_filter = repository_filter

    def enumerate_definition_descriptors(self):
        executor = ThreadPoolExecutor(max_workers=16)

        # scan github repositories
        for github_org_cfg in self.job_mapping.github_organisations():
            github_cfg = self.cfg_set.github(github_org_cfg.github_cfg_name())
            github_org_name = github_org_cfg.org_name()
            info('scanning github organisation {gho}'.format(gho=github_org_name))

            github_api = ccc.github.github_api(github_cfg)
            github_org = github_api.organization(github_org_name)

            scan_repository_for_definitions = functools.partial(
                self._scan_repository_for_definitions,
                github_cfg=github_cfg,
                org_name=github_org_name,
            )

            matching_repositories = (
                repo for repo in github_org.repositories()
                if github_org_cfg.repository_matches(repo.name)
                   and (not self.repository_filter or self.repository_filter(repo.name))
            )

            for definition_descriptors in executor.map(
                scan_repository_for_definitions,
                matching_repositories,
            ):
                yield from definition_descriptors


class DefinitionDescriptor:
    '''
    Container type holding the result of a pipeline rendering and additional
    metadata required to deploy as concrete Concourse Pipeline.
    '''
    def __init__(
        self,
        pipeline_name,
        pipeline_definition,
        main_repo,
        concourse_target_cfg,
        concourse_target_team,
        override_definitions=[{},],
        exception=None,
    ):
        try:
            self.pipeline_name = not_empty(pipeline_name)
            self.pipeline_definition = not_none(pipeline_definition)
            self.main_repo = not_none(main_repo)
            self.concourse_target_cfg = not_none(concourse_target_cfg)
            self.concourse_target_team = not_none(concourse_target_team)
            self.override_definitions = not_none(override_definitions)
        except Exception as e:
            raise ValueError(
                f'{e=} missing value: {pipeline_name=} {pipeline_definition=} {main_repo=} '
                f'{concourse_target_cfg=} {concourse_target_team=} {override_definitions=}'
            )
        self.exception = exception

    def template_name(self):
        return self.pipeline_definition.get('template', 'default')

    def effective_pipeline_name(self):
        return '-'.join((self.pipeline_name, self.main_repo['branch']))

    def concourse_target(self):
        return (self.concourse_target_cfg, self.concourse_target_team)

    def concourse_target_key(self):
        return '{n}:{t}'.format(
            n=self.concourse_target_cfg.name(),
            t=self.concourse_target_team
        )

    def __repr__(self):
        return f'{self.__class__}: {self.pipeline_name}'


class TemplateRetriever:
    '''
    Provides mako templates by name. Templates are cached.
    '''

    def __init__(self, template_path=concourse.paths.template_dir):
        if type(template_path) == str:
            self.template_path = (template_path,)
        else:
            self.template_path = template_path

    @functools.lru_cache()
    def template_file(self, template_name):
        # TODO: do not hard-code file name extension
        template_file_name = template_name + '.mako'
        for path in self.template_path:
            for dirpath, _, filenames in os.walk(path):
                if template_file_name in filenames:
                    return os.path.join(dirpath, template_file_name)
        fail(
            'could not find template {t}, tried in {p}'.format(
                t=str(template_name),
                p=','.join(map(str, self.template_path))
            )
        )

    @functools.lru_cache()
    def template_contents(self, template_name):
        with open(self.template_file(template_name=template_name)) as f:
            return f.read()
