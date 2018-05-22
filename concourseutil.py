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

from copy import copy
from ensure import ensure_annotations
from github3.github import GitHubEnterprise, GitHub
from urllib.parse import urlparse, parse_qs

from util import ctx
from util import parse_yaml_file, info, fail, which, warning, CliHints, CliHint
from util import ctx as global_ctx
from concourse import pipelines
import concourse.client as concourse
import concourse.setup as setup
from model import ConfigFactory, ConcourseTeamCredentials
import kubeutil
from kubeutil import KubernetesNamespaceHelper
import github
from githubutil import _create_github_api_object


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


def render_secrets(cfg_dir: CliHints.existing_dir(), cfg_name: str, out_file: str, external_url: str = None):
    fail('currrently no longer implemented')
    # todo: serialise configuration set or rm function


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
        for rendered_pipeline, definition, pipeline_args in pipelines.generate_pipelines(
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

    pipelines.deploy_pipeline(
        pipeline_definition=pipeline_definition,
        pipeline_name=pipeline_name,
        concourse_cfg=concourse_cfg,
        team_credentials=team_credentials,
    )


def _list_github_resources(
  concourse_url:str,
  concourse_user:str='kubernetes',
  concourse_passwd:str='kubernetes',
  concourse_team:str='kubernetes',
  concourse_pipelines=None,
  github_url:str=None,
):
    concourse_api = concourse.ConcourseApi(base_url=concourse_url, team_name=concourse_team)
    concourse_api.login(
      team=concourse_team,
      username=concourse_user,
      passwd=concourse_passwd
    )
    github_hostname = urlparse(github_url).netloc
    pipeline_names = concourse_pipelines if concourse_pipelines else concourse_api.pipelines()
    for pipeline_name in pipeline_names:
        pipeline_cfg = concourse_api.pipeline_cfg(pipeline_name)
        resources = pipeline_cfg.resources
        resources = filter(lambda r: r.has_webhook_token(), resources)
        # only process repositories from concourse's "default" github repository
        resources = filter(lambda r: r.github_source().hostname() == github_hostname, resources)

        yield from resources


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

def sync_webhooks(
  github_cfg:'GithubConfig',
  concourse_cfg:'ConcourseConfig',
  concourse_team:str='kubernetes',
  concourse_pipelines:[str]=None,
  concourse_verify_ssl:bool=False,
):
    concourse_url = concourse_cfg.external_url()
    concourse_proxy_url = concourse_cfg.external_url()
    team_cfg = concourse_cfg.team_credentials(concourse_team)
    concourse_user = team_cfg.username()
    concourse_passwd = team_cfg.passwd()

    github_resources = _list_github_resources(
      concourse_url=concourse_url,
      concourse_user=concourse_user,
      concourse_passwd=concourse_passwd,
      concourse_team=concourse_team,
      concourse_pipelines=concourse_pipelines,
      github_url=github_cfg.http_url(),
    )
    # group by repositories
    path_to_resources = {}
    for gh_res in github_resources:
        repo_path = gh_res.github_source().repo_path()
        if not repo_path in path_to_resources:
            path_to_resources[repo_path] = [gh_res]
        else:
            path_to_resources[repo_path].append(gh_res)

    github_obj = _create_github_api_object(github_cfg=github_cfg, webhook_user=True)

    webhook_syncer = github.GithubWebHookSyncer(github_obj)
    failed_hooks = 0

    for repo, resources in path_to_resources.items():
        try:
            _sync_webhook(
              resources=resources,
              webhook_syncer=webhook_syncer,
              concourse_cfg=concourse_cfg,
              concourse_proxy_url=concourse_proxy_url,
              skip_ssl_validation=not concourse_verify_ssl
            )
        except RuntimeError as rte:
            failed_hooks += 1
            info(str(rte))

    if failed_hooks is not 0:
        fail('{n} webhooks could not be updated or created!'.format(n=failed_hooks))


def _sync_webhook(
  resources: [concourse.Resource],
  webhook_syncer: github.GithubWebHookSyncer,
  concourse_cfg: 'ConcourseConfig',
  concourse_proxy_url: str,
  skip_ssl_validation: bool=False
):
    first_res = resources[0]
    first_github_src = first_res.github_source()
    pipeline = first_res.pipeline

    # construct webhook endpoint
    routes = copy(pipeline.concourse_api.routes)
    # workaround: direct webhooks against delaying proxy
    routes.base_url = concourse_proxy_url
    repository = first_github_src.parse_repository()
    organisation = first_github_src.parse_organisation()

    # collect callback URLs
    def webhook_url(gh_res):
        github_src = gh_res.github_source()
        webhook_url = routes.resource_check_webhook(
          pipeline_name=pipeline.name,
          resource_name=gh_res.name,
          webhook_token=gh_res.webhook_token(),
          concourse_id=concourse_cfg.name(),
        )
        return webhook_url

    webhook_urls = set(map(webhook_url, resources))

    webhook_syncer.add_or_update_hooks(
      owner=organisation,
      repository_name=repository,
      callback_urls=webhook_urls,
      skip_ssl_validation=skip_ssl_validation
    )

    def url_filter(url):
        concourse_id = parse_qs(urlparse(url).query).get('concourse_id')
        return concourse_id and concourse_cfg.name() in concourse_id

    processed, removed = webhook_syncer.remove_outdated_hooks(
      owner=organisation,
      repository_name=repository,
      urls_to_keep=webhook_urls,
      # only process webhooks that were created by "us"
      url_filter_fun=url_filter,
    )
    info('updated {c} hook(s) for: {o}/{r}'.format(
        c=len(webhook_urls),
        o=organisation,
        r=repository
        )
    )
    if removed > 0:
        info('removed {c} outdated hook(s)'.format(c=removed))


def diff_pipelines(left_file: CliHints.yaml_file(), right_file: CliHints.yaml_file()):
    from deepdiff import DeepDiff
    from pprint import pprint

    diff = DeepDiff(left_file, right_file, ignore_order=True)
    if diff:
        pprint(diff)
        fail('diffs were found')
    else:
        info('the yaml documents are equivalent')

