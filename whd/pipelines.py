# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import flask.current_app

import concourse.enumerator
import concourse.replicator

logger = flask.current_app.logger


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

    # no need for parallelisation
    definition_descriptors = repo_enumerator.enumerate_definition_descriptors()
    preprocessed_descriptors = map(
        preprocessor.process_definition_descriptor,
        definition_descriptors,
    )
    render_results = map(
        renderer.render,
        preprocessed_descriptors,
    )
    for render_result in render_results:
        if not render_result.render_status == concourse.replicator.RenderStatus.SUCCEEDED:
            logger.warning('failed to render pipeline - ignoring')
            continue
        deploy_result = deployer.deploy(render_result.definition_descriptor)
        if deploy_result.deploy_status == concourse.replicator.DeployStatus.SUCCEEDED:
            logger.info('successfully rendered and deployed pipeline')
        else:
            logger.warning('failed to deploy a pipeline')
