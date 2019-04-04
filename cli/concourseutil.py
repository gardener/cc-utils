# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

import os
import yaml

import kube.ctx
import landscape_setup.concourse as setup_concourse

from util import ctx
from util import (
    info,
    fail,
    CliHints,
    CliHint,
)
from concourse import client
from concourse.util import (
    sync_org_webhooks,
    resurrect_pods,
)
from concourse.enumerator import (
    DefinitionDescriptorPreprocessor,
    GithubOrganisationDefinitionEnumerator,
    SimpleFileDefinitionEnumerator,
    TemplateRetriever,
)
from concourse.replicator import (
    FilesystemDeployer,
    PipelineReplicator,
    Renderer,
)


def update_certificate(
    tls_config_name: CliHint(typehint=str, help="TLS config element name to update"),
    certificate_file: CliHints.existing_file(help="certificate file path"),
    key_file: CliHints.existing_file(help="private key file path"),
    output_path: CliHints.existing_dir(help="TLS config file output path")
):
    # Stuff used for yaml formatting, when dumping a dictionary
    class LiteralStr(str):
        """Used to create yaml block style indicator | """

    def literal_str_representer(dumper, data):
        """Used to create yaml block style indicator"""
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')

    # read new certificate data
    certificate_file = os.path.abspath(certificate_file)
    private_key_file = os.path.abspath(key_file)
    with open(certificate_file) as f:
        certificate = f.read()
    with open(private_key_file) as f:
        private_key = f.read()

    # set new certificate data to specified argument 'tls_config_name'
    cfg_factory = ctx().cfg_factory()
    tls_config_element = cfg_factory.tls_config(tls_config_name)
    tls_config_element.set_private_key(private_key)
    tls_config_element.set_certificate(certificate)

    # patch tls config dict so that yaml.dump outputs literal strings using '|'
    yaml.add_representer(LiteralStr, literal_str_representer)
    configs = cfg_factory._configs('tls_config')
    for k1, v1 in configs.items():
        for k2, _ in v1.items():
            configs[k1][k2] = LiteralStr(configs[k1][k2])

    # dump updated tls config to given output path
    tls_config_type = cfg_factory._cfg_types()['tls_config']
    tls_config_file = list(tls_config_type.sources())[0].file()
    with open(os.path.join(output_path, tls_config_file), 'w') as f:
        yaml.dump(configs, f, indent=2, default_flow_style=False)


def render_pipeline(
    definition_file: CliHints.existing_file(),
    template_path: CliHints.existing_dir(),
    cfg_name: str,
    out_dir: CliHints.existing_dir(),
    template_include_dir: str=None,
):
    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_name=cfg_name)

    def_enumerators = [
        SimpleFileDefinitionEnumerator(
            definition_file=definition_file,
            cfg_set=cfg_set,
            repo_path='example/example',
            repo_branch='master',
            repo_host='github.com',
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
        template_path: str,
        config_name: str,
        out_dir: str,
        template_include_dir: str = None,
):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    cfg_factory = ctx().cfg_factory()
    config_set = cfg_factory.cfg_set(cfg_name=config_name)

    concourse_cfg = config_set.concourse()
    job_mapping_set = cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())

    if not template_include_dir:
        template_include_dir = template_path

    def_enumerators = []
    for job_mapping in job_mapping_set.job_mappings().values():
        def_enumerators.append(
            GithubOrganisationDefinitionEnumerator(
                job_mapping=job_mapping,
                cfg_set=config_set
            )
        )

    preprocessor = DefinitionDescriptorPreprocessor()

    template_retriever = TemplateRetriever(template_path=[template_path])
    renderer = Renderer(
        template_retriever=template_retriever,
        template_include_dir=template_include_dir,
        cfg_set=config_set,
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


def diff_pipelines(left_file: CliHints.yaml_file(), right_file: CliHints.yaml_file()):
    from deepdiff import DeepDiff
    from pprint import pprint

    diff = DeepDiff(left_file, right_file, ignore_order=True)
    if diff:
        pprint(diff)
        fail('diffs were found')
    else:
        info('the yaml documents are equivalent')


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
    team_credentials = concourse_cfg.team_credentials(team_name)
    api = client.from_cfg(
        concourse_cfg=concourse_cfg,
        team_name=team_credentials.teamname(),
    )
    api.trigger_resource_check(
        pipeline_name=pipeline_name,
        resource_name=resource_name,
    )


def set_teams(
    config_name: CliHint(typehint=str, help='the cfg_set name to use'),
):
    config_factory = ctx().cfg_factory()
    config_set = config_factory.cfg_set(cfg_name=config_name)
    config = config_set.concourse()

    setup_concourse.set_teams(config=config)


def start_worker_resurrector(
    config_name: CliHint(typehint=str, help='the config set name to use'),
    concourse_namespace='concourse',
    worker_label_selector='app=concourse-worker',
):
    config_factory = ctx().cfg_factory()
    config_set = config_factory.cfg_set(cfg_name=config_name)
    kubernetes_cfg = config_set.kubernetes()
    kube_client = kube.ctx.Ctx()
    kube_client.set_kubecfg(kubernetes_cfg.kubeconfig())

    concourse_cfg = config_set.concourse()
    concourse_client = client.from_cfg(concourse_cfg=concourse_cfg, team_name='main')

    resurrect_pods(
        namespace=concourse_namespace,
        label_selector=worker_label_selector,
        concourse_client=concourse_client,
        kubernetes_client=kube_client
    )
