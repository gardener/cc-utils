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

import os
import yaml

from util import ctx
from util import (
    info,
    fail,
    which,
    warning,
    CliHints,
    CliHint,
)
import concourse.setup as setup

from concourse import client
from concourse.util import sync_webhooks
from concourse.enumerator import (
    DefinitionDescriptorPreprocessor,
    GithubOrganisationDefinitionEnumerator,
    MappingfileDefinitionEnumerator,
    SimpleFileDefinitionEnumerator,
    TemplateRetriever,
)
from concourse.replicator import (
    FilesystemDeployer,
    PipelineReplicator,
    Renderer,
    ReplicationResultProcessor,
)


def __add_module_command_args(parser):
    parser.add_argument('--kubeconfig', required=False)
    return parser


def deploy_or_upgrade_concourse(
    config_name: CliHint(typehint=str, help="the cfg_set to use"),
    deployment_name: CliHint(typehint=str, help="namespace and deployment name")='concourse',
    timeout_seconds: CliHint(typehint=int, help="how long to wait for concourse startup")=180,
    dry_run: bool=True,
):
    '''Deploys a new concourse-instance using the given deployment name and config-directory.'''
    which("helm")

    _display_info(
        dry_run=dry_run,
        operation="DEPLOYED",
        deployment_name=deployment_name,
    )

    if dry_run:
        return

    setup.deploy_concourse_landscape(
        config_name=config_name,
        deployment_name=deployment_name,
        timeout_seconds=timeout_seconds,
    )


def destroy_concourse(
    config_name: CliHint(typehint=str, help="The config set to use"),
    release_name: CliHint(typehint=str, help="namespace and deployment name")='concourse',
    dry_run: bool = True
):
    '''Destroys a concourse-instance using the given helm release name'''

    _display_info(
        dry_run=dry_run,
        operation="DESTROYED",
        deployment_name=release_name,
    )

    if dry_run:
        return

    setup.destroy_concourse_landscape(
        config_name=config_name,
        release_name=release_name
    )


def set_teams(
    config_name: CliHint(typehint=str, help='the cfg_set name to use'),
):
    config_factory = ctx().cfg_factory()
    config_set = config_factory.cfg_set(cfg_name=config_name)
    config = config_set.concourse()

    setup.set_teams(config=config)


def _display_info(dry_run: bool, operation: str, **kwargs):
    info("Concourse will be {o} using helm with the following arguments".format(o=operation))
    max_leng = max(map(len, kwargs.keys()))
    for k, v in kwargs.items():
        key_str = k.ljust(max_leng)
        info("{k}: {v}".format(k=key_str, v=v))

    if dry_run:
        warning("this was a --dry-run. Set the --no-dry-run flag to actually deploy")


def update_certificate(
    tls_config_name: CliHint(typehint=str, help="TLS config element name to update"),
    certificate_file: CliHints.existing_file(help="certificate file path"),
    key_file: CliHints.existing_file(help="private key file path"),
    output_path: CliHints.existing_dir(help="TLS config file output path")
):
    # Stuff used for yaml formatting, when dumping a dictionary
    class LiteralStr(str):
        """Used to create yaml block style indicator | """
        pass

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
        for k2, v2 in v1.items():
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
        definitions_root_dir: str,
        template_path: [str],
        config_name: str,
        template_include_dir: str,
        out_dir: str
    ):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    cfg_factory = ctx().cfg_factory()
    config_set = cfg_factory.cfg_set(cfg_name=config_name)

    concourse_cfg = config_set.concourse()
    job_mapping_set = cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())

    def_enumerators = []
    for job_mapping in job_mapping_set.job_mappings().values():
        def_enumerators.append(
            GithubOrganisationDefinitionEnumerator(
                job_mapping=job_mapping,
                cfg_set=config_set
            )
        )
        if job_mapping.definition_dirs():
            def_enumerators.append(
                MappingfileDefinitionEnumerator(
                    base_dir=definitions_root_dir,
                    job_mapping=job_mapping,
                    cfg_set=config_set,
                )
            )

    preprocessor = DefinitionDescriptorPreprocessor()

    template_retriever = TemplateRetriever(template_path=template_path)
    renderer = Renderer(
        template_retriever=template_retriever,
        template_include_dir=template_include_dir,
        cfg_set=config_set,
    )

    deployer = FilesystemDeployer(base_dir=out_dir)

    result_processor = ReplicationResultProcessor(cfg_set=config_set)

    replicator = PipelineReplicator(
        definition_enumerators=def_enumerators,
        descriptor_preprocessor=preprocessor,
        definition_renderer=renderer,
        definition_deployer=deployer,
        result_processor=result_processor,
    )

    replicator.replicate()


def sync_webhooks_from_cfg(
    cfg_name: str,
):
    '''
    convenience wrapper for sync_webhooks for local usage with cc-config repo
    '''
    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_name)
    github_cfg = cfg_set.github()
    concourse_cfg = cfg_set.concourse()
    job_mapping_set = cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())

    for job_mapping in job_mapping_set.job_mappings().values():
        team_name = job_mapping.team_name()
        info("syncing webhooks (team: {})".format(team_name))
        team = concourse_cfg.team_credentials(team_name)
        sync_webhooks(
          github_cfg=github_cfg,
          concourse_cfg=concourse_cfg,
          job_mapping=job_mapping,
          concourse_team_credentials=team,
        )


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
    api = client.ConcourseApi(
        base_url=concourse_cfg.external_url(),
        team_name=team_credentials.teamname(),
    )
    api.login(
        team_credentials.teamname(),
        team_credentials.username(),
        team_credentials.passwd(),
    )
    api.trigger_resource_check(
        pipeline_name=pipeline_name,
        resource_name=resource_name,
    )
