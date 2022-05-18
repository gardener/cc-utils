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
import urllib.parse
import logging

from ci.util import (
    ctx,
    CliHints,
)

from concourse.util import sync_org_webhooks
from concourse.enumerator import (
    DefinitionDescriptorPreprocessor,
    GithubOrganisationDefinitionEnumerator,
    SimpleFileDefinitionEnumerator,
    TemplateRetriever,
)
from concourse.replicator import (
    FilesystemDeployer,
    PipelineReplicator,
    RenderOrigin,
    Renderer,
)

import ccc.concourse
import ci.log
import concourse.model as ccm


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


own_dir = os.path.abspath(os.path.dirname(__file__))
repo_root = os.path.abspath(
    os.path.join(
        own_dir,
        os.pardir,
        os.pardir,
    )
)

__cmd_name__ = 'concourse'


def _template_path():
    return os.path.join(
        repo_root,
        'concourse',
    )


def render_pipeline(
    definition_file: CliHints.existing_file(),
    cfg_name: str,
    out_dir: CliHints.existing_dir(),
    repo_path: str = 'example/example',
    repo_branch: str = 'master',
    repo_host: str = 'github.com',
    template_path: str=_template_path(),
    template_include_dir: str=None,
):
    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_name=cfg_name)
    logger.info(f'Template path: {template_path}')

    repo_url = urllib.parse.urlunparse(('https', repo_host, repo_path, '', '', ''))
    try:
        job_mapping = cfg_set.job_mapping().job_mapping_for_repo_url(repo_url, cfg_set)
        secret_cfg = cfg_factory.secret(job_mapping.secret_cfg())
    except ValueError as e:
        logger.warning(f'An error occurred: {e}. Will use dummy values to render pipeline.')
        job_mapping = None
        secret_cfg = None

    def_enumerators = [
        SimpleFileDefinitionEnumerator(
            definition_file=definition_file,
            cfg_set=cfg_set,
            repo_path=repo_path,
            repo_branch=repo_branch,
            repo_host=repo_host,
            job_mapping=job_mapping,
            secret_cfg=secret_cfg,
        )
    ]

    preprocessor = DefinitionDescriptorPreprocessor()

    if not template_include_dir:
        template_include_dir = template_path

    template_retriever = TemplateRetriever(template_path=template_path)
    renderer = Renderer(
        template_retriever=template_retriever,
        template_include_dir=template_include_dir,
        cfg_set=cfg_set,
        render_origin=RenderOrigin.LOCAL,
    )

    deployer = FilesystemDeployer(base_dir=out_dir)

    replicator = PipelineReplicator(
        definition_enumerators=def_enumerators,
        descriptor_preprocessor=preprocessor,
        definition_renderer=renderer,
        definition_deployer=deployer
    )

    replicator.replicate()


def render_pipelines(
        cfg_name: str,
        out_dir: str,
        template_path: str=_template_path(),
        org: str=None, # if set, filter for org
        repo: str=None, # if set, filter for repo
):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    cfg_factory = ctx().cfg_factory()
    config_set = cfg_factory.cfg_set(cfg_name=cfg_name)

    concourse_cfg = config_set.concourse()
    job_mapping_set = cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())

    template_include_dir = template_path

    if repo:
        repository_filter = lambda repo_obj: repo_obj.name == repo
    else:
        repository_filter = None

    def_enumerators = []
    for job_mapping in job_mapping_set.job_mappings().values():
        job_mapping: ccm.JobMapping

        if org and not org in {oc.org_name() for oc in job_mapping.github_organisations()}:
            continue

        def_enumerators.append(
            GithubOrganisationDefinitionEnumerator(
                job_mapping=job_mapping,
                cfg_set=config_set,
                repository_filter=repository_filter,
            )
        )

    preprocessor = DefinitionDescriptorPreprocessor()

    template_retriever = TemplateRetriever(template_path=[template_path])
    renderer = Renderer(
        template_retriever=template_retriever,
        template_include_dir=template_include_dir,
        cfg_set=config_set,
        render_origin=RenderOrigin.LOCAL,
    )

    deployer = FilesystemDeployer(base_dir=out_dir)

    replicator = PipelineReplicator(
        definition_enumerators=def_enumerators,
        descriptor_preprocessor=preprocessor,
        definition_renderer=renderer,
        definition_deployer=deployer,
    )

    replicator.replicate()


def sync_org_webhooks_from_cfg(
    whd_deployment_config_name: str,
):
    '''
    Set or update all org-webhooks for the given configs.
    '''
    cfg_factory = ctx().cfg_factory()
    whd_deployment_cfg = cfg_factory.webhook_dispatcher_deployment(whd_deployment_config_name)
    sync_org_webhooks(whd_deployment_cfg)


def trigger_resource_check(
    cfg_name: CliHints.non_empty_string(help="cfg_set to use"),
    team_name: CliHints.non_empty_string(help="pipeline's team name"),
    pipeline_name: CliHints.non_empty_string(help="pipeline name"),
    resource_name: CliHints.non_empty_string(help="resource to check"),
):
    '''Triggers a check of the specified Concourse resource
    '''
    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_name)
    concourse_cfg = cfg_set.concourse()

    api = ccc.concourse.client_from_cfg_name(
        concourse_cfg_name=concourse_cfg.name(),
        team_name=team_name,
    )
    api.trigger_resource_check(
        pipeline_name=pipeline_name,
        resource_name=resource_name,
    )
