# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import concourse.enumerator
import concourse.replicator


def update_repository_pipelines(
    repo_url,
    cfg_set,
    whd_cfg,
):
    repo_enumerator = concourse.enumerator.GithubRepositoryDefinitionEnumerator(
        repository_url=repo_url,
        cfg_set=cfg_set,
    )
    preprocessor = concourse.enumerator.DefinitionDescriptorPreprocessor()
    template_retriever = concourse.enumerator.TemplateRetriever(
        template_path=whd_cfg.pipeline_templates_path(),
    )
    renderer = concourse.replicator.Renderer(
        template_retriever=template_retriever,
        template_include_dir=whd_cfg.pipeline_include_path(),
        cfg_set=cfg_set,
    )
    deployer = concourse.replicator.ConcourseDeployer(
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

    replicator.replicate()
