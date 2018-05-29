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
import subprocess

from util import ctx
from util import info, fail, which, warning, CliHints, CliHint
from util import ctx as global_ctx
from concourse.pipelines import replicator
import concourse.setup as setup
from concourse.util import sync_webhooks
from model import ConfigFactory
import kubeutil
from kube.helper import KubernetesNamespaceHelper


def __add_module_command_args(parser):
    parser.add_argument('--kubeconfig', required=False)
    return parser


def deploy_or_upgrade_concourse(
    config_name: CliHint(typehint=str, help="Which of the configurations contained in --config-dir to use."),
    deployment_name: CliHint(typehint=str, help="Name under which Concourse will be deployed. Will also be the identifier of the namespace into which it is deployed.")='concourse',
    timeout_seconds: CliHint(typehint=int, help="Maximum time (in seconds) to wait after deploying for the Concourse-webserver to become available.")=180,
    dry_run: bool=True,
):
    '''Deploys a new concourse-instance using the given deployment name and config-directory.'''
    which("helm")

    namespace = deployment_name
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


def destroy_concourse(release: str, dry_run: bool = True):
    _display_info(
        dry_run=dry_run,
        operation="DESTROYED",
        deployment_name=release,
    )

    if dry_run:
        return

    helm_executable = which("helm")
    context = kubeutil.Ctx()
    namespace_helper = KubernetesNamespaceHelper(context.create_core_api())
    namespace_helper.delete_namespace(namespace=release)
    helm_env = os.environ.copy()

    # pylint: disable=no-member
    # Check for optional arg --kubeconfig
    cli_args = global_ctx().args
    if cli_args and hasattr(cli_args, 'kubeconfig') and cli_args.kubeconfig:
        helm_env['KUBECONFIG'] = cli_args.kubeconfig
    # pylint: enable=no-member

    subprocess.run([helm_executable, "delete", release, "--purge"], env=helm_env)


def set_teams(
    config_name: CliHint(typehint=str, help='Which of the configurations contained in "--config-file" to use.'),
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

    for job_mapping in job_mapping_set.job_mappings().values():
        for rendered_pipeline, definition, pipeline_args in replicator.generate_pipelines(
                definitions_root_dir=definitions_root_dir,
                job_mapping=job_mapping,
                template_path=template_path,
                template_include_dir=template_include_dir,
                config_set=config_set
            ):
            out_name = os.path.join(out_dir, pipeline_args.name + '.yaml')
            with open(out_name, 'w') as f:
                f.write(rendered_pipeline)


def deploy_pipeline(
        pipeline_file: CliHint('generated pipeline definition to deploy'),
        pipeline_name: CliHint('the name under which the pipeline shall be deployed'),
        team_name: CliHint('name of the target team'),
        config_dir: CliHints.existing_dir('directory containing Concourse configuration'),
        config_name: CliHint('identifier of the configuration in the config directory to use')
):
    cfg_factory = ConfigFactory.from_cfg_dir(cfg_dir=config_dir)
    concourse_cfg = cfg_factory.concourse(config_name)
    team_credentials = concourse_cfg.team_credentials(team_name)

    with open(pipeline_file) as f:
        pipeline_definition = f.read()

    replicator.deploy_pipeline(
        pipeline_definition=pipeline_definition,
        pipeline_name=pipeline_name,
        concourse_cfg=concourse_cfg,
        team_credentials=team_credentials,
    )


def sync_webhooks_from_cfg(
    team_name: str,
    cfg_name: str,
):
    '''
    convenience wrapper for sync_webhooks for local usage with cc-config repo
    '''
    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_name)
    github_cfg = cfg_set.github()
    github_cred = github_cfg.credentials()
    concourse_cfg = cfg_set.concourse()
    team_cfg = concourse_cfg.team_credentials(team_name)

    sync_webhooks(
      github_cfg=github_cfg,
      concourse_cfg=concourse_cfg,
      concourse_team=team_cfg.teamname(),
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

