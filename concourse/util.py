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

import github.webhook
from github.util import _create_github_api_object
import concourse.client as concourse
from model import ConcourseTeamCredentials, ConcourseConfig, GithubConfig, JobMapping
from util import parse_yaml_file, info, fail, which, warning, CliHints, CliHint


def list_github_resources(
    concourse_url: str,
    concourse_user: str='kubernetes',
    concourse_passwd: str='kubernetes',
    concourse_team: str='kubernetes',
    concourse_pipelines=None,
    github_url: str=None,
):
    concourse_api = concourse.ConcourseApi(base_url=concourse_url, team_name=concourse_team)
    concourse_api.login(
        team=concourse_team,
        username=concourse_user,
        passwd=concourse_passwd
    )
    pipeline_names = concourse_pipelines if concourse_pipelines else concourse_api.pipelines()
    yield from filter(
      lambda r: r.has_webhook_token(),
      concourse_api.pipeline_resources(pipeline_names=pipeline_names),
    )


def sync_webhooks(
    github_cfg: GithubConfig,
    concourse_cfg: ConcourseConfig,
    job_mapping: JobMapping,
    concourse_team_credentials: ConcourseTeamCredentials,
    concourse_pipelines: [str]=None,
    concourse_verify_ssl: bool=False,
):
    concourse_url = concourse_cfg.external_url()
    concourse_team = concourse_team_credentials.teamname()
    concourse_user = concourse_team_credentials.username()
    concourse_passwd = concourse_team_credentials.passwd()

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

    github_obj = _create_github_api_object(github_cfg=github_cfg)

    webhook_syncer = github.webhook.GithubWebHookSyncer(github_obj)
    failed_hooks = 0

    for repo, resources in path_to_resources.items():
        try:
            _sync_webhook(
                resources=resources,
                webhook_syncer=webhook_syncer,
                job_mapping_name=job_mapping.name(),
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
    webhook_syncer: github.webhook.GithubWebHookSyncer,
    job_mapping_name: str,
    concourse_cfg: 'ConcourseConfig',
    skip_ssl_validation: bool=False
):
    first_res = resources[0]
    first_github_src = first_res.github_source()
    pipeline = first_res.pipeline

    # construct webhook endpoint
    routes = copy(pipeline.concourse_api.routes)

    # workaround: direct webhooks against delaying proxy if configured
    if concourse_cfg.deploy_delaying_proxy():
        routes.base_url = concourse_cfg.proxy_url()

    repository = first_github_src.parse_repository()
    organisation = first_github_src.parse_organisation()

    # collect callback URLs
    def webhook_url(gh_res):
        github_src = gh_res.github_source()
        query_attributes = github.webhook.WebhookQueryAttributes(
            webhook_token=gh_res.webhook_token(),
            concourse_id=concourse_cfg.name(),
            job_mapping_id=job_mapping_name,
        )
        webhook_url = routes.resource_check_webhook(
            pipeline_name=gh_res.pipeline.name,
            resource_name=gh_res.name,
            query_attributes=query_attributes,
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
        parsed_url = parse_qs(urlparse(url).query)
        concourse_id = parsed_url.get(github.webhook.WebhookQueryAttributes.CONCOURSE_ID_ATTRIBUTE_NAME)
        job_mapping_id = parsed_url.get(github.webhook.WebhookQueryAttributes.JOB_MAPPING_ID_ATTRIBUTE_NAME)
        # we consider an url for removal iff it contains parameters 'concourse_id' and 'job_mapping_id'
        # that match the configured concourse_id and job_mapping_name
        return (
            concourse_id is not None and
            concourse_cfg.name() in concourse_id and
            job_mapping_id is not None and
            job_mapping_name in job_mapping_id
        )

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
