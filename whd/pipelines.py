# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import logging

import concourse.enumerator
import concourse.paths
import concourse.replicator
import model
import model.concourse
import model.webhook_dispatcher

logger = logging.getLogger(__name__)


def validate_repository_pipelines(
    repo_url: str,
    cfg_set: model.ConfigurationSet,
    whd_cfg: model.webhook_dispatcher.WebhookDispatcherConfig,
    branch: str=None,
    job_mapping=None,
):
    logger.info(f'validating pipeline(s) for {repo_url=} using {cfg_set.name()}')

    preprocessor = concourse.enumerator.DefinitionDescriptorPreprocessor()
    template_retriever = concourse.enumerator.TemplateRetriever()
    renderer = concourse.replicator.Renderer(
        template_retriever=template_retriever,
        template_include_dir=concourse.paths.template_include_dir,
        cfg_set=cfg_set,
        render_origin=concourse.replicator.RenderOrigin.WEBHOOK_DISPATCHER,
    )

    repo_enumerator = concourse.enumerator.GithubRepositoryDefinitionEnumerator(
        repository_url=repo_url,
        cfg_set=cfg_set,
        branch=branch,
        job_mapping=job_mapping,
    )
    deployer = concourse.replicator.NoOpDeployer()
    results_processor = concourse.replicator.PipelineValidationResultProcessor()

    replicator = concourse.replicator.PipelineReplicator(
        definition_enumerators=[repo_enumerator],
        descriptor_preprocessor=preprocessor,
        definition_renderer=renderer,
        definition_deployer=deployer,
        result_processor=results_processor,
    )

    logger.info('awaiting validation results')
    replicator.replicate()


def replicate_repository_pipelines(
    repo_url: str,
    cfg_set: model.ConfigurationSet,
    whd_cfg: model.webhook_dispatcher.WebhookDispatcherConfig,
):
    logger.info(f'replicating pipeline(s) for {repo_url=} using {cfg_set.name()}')

    preprocessor = concourse.enumerator.DefinitionDescriptorPreprocessor()
    template_retriever = concourse.enumerator.TemplateRetriever()
    renderer = concourse.replicator.Renderer(
        template_retriever=template_retriever,
        template_include_dir=concourse.paths.template_include_dir,
        cfg_set=cfg_set,
        render_origin=concourse.replicator.RenderOrigin.WEBHOOK_DISPATCHER,
    )

    repo_enumerator = concourse.enumerator.GithubRepositoryDefinitionEnumerator(
        repository_url=repo_url,
        cfg_set=cfg_set,
    )
    deployer = concourse.replicator.ConcourseDeployer(
        cfg_set=cfg_set,
        unpause_pipelines=False,
        expose_pipelines=True,
    )
    results_processor = concourse.replicator.ReplicationResultProcessor(
        cfg_set=cfg_set,
        unpause_new_pipelines=True,
        remove_pipelines=False,
        reorder_pipelines=False,
    )

    replicator = concourse.replicator.PipelineReplicator(
        definition_enumerators=[repo_enumerator],
        descriptor_preprocessor=preprocessor,
        definition_renderer=renderer,
        definition_deployer=deployer,
        result_processor=results_processor,
    )

    logger.info('awaiting replication-results')
    result = replicator.replicate()
    logger.info(f'{result=}')
