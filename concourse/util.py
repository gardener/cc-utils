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

from urllib.parse import urlparse, parse_qs
from copy import copy

import github
from github.util import _create_github_api_object
import concourse.client as concourse
from util import parse_yaml_file, info, fail, which, warning, CliHints, CliHint


def list_github_resources(
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


def sync_webhooks(
  github_cfg:'GithubConfig',
  concourse_cfg:'ConcourseConfig',
  concourse_team:str='kubernetes',
  concourse_pipelines:[str]=None,
  concourse_verify_ssl:bool=False,
):
    concourse_url = concourse_cfg.external_url()
    team_cfg = concourse_cfg.team_credentials(concourse_team)
    concourse_user = team_cfg.username()
    concourse_passwd = team_cfg.passwd()

    github_resources = list_github_resources(
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
  skip_ssl_validation: bool=False
):
    first_res = resources[0]
    first_github_src = first_res.github_source()
    pipeline = first_res.pipeline

    # construct webhook endpoint
    routes = copy(pipeline.concourse_api.routes)

    # workaround: direct webhooks against delaying proxy
    routes.base_url = concourse_cfg.proxy_url()

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
