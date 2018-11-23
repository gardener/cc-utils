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

from urllib.parse import urlparse, parse_qs, urlunparse

import github.webhook
from github.util import _create_github_api_object
import concourse.client as concourse
import concourse.client.model
from model.concourse import (
    JobMapping,
    JobMappingSet,
)
from model.webhook_dispatcher import WebhookDispatcherConfig, WebhookDispatcherDeploymentConfig
from util import info, warning, fail, ctx, merge_dicts


def list_github_resources(
    concourse_cfg,
    concourse_team: str='kubernetes',
    concourse_pipelines=None,
    github_url: str=None,
):
    concourse_api = concourse.from_cfg(concourse_cfg=concourse_cfg, team_name=concourse_team)
    pipeline_names = concourse_pipelines if concourse_pipelines else concourse_api.pipelines()
    yield from filter(
      lambda r: r.has_webhook_token(),
      concourse_api.pipeline_resources(pipeline_names=pipeline_names),
    )


def sync_webhooks(
    webhook_dispatcher_cfg: WebhookDispatcherConfig,
    # TODO: There is currently nothing explicitly linking a whd-cfg and a whd-deployment-cfg
    webhook_dispatcher_deployment_cfg: WebhookDispatcherDeploymentConfig,
):
    cfg_factory = ctx().cfg_factory()

    concourse_cfg_names = webhook_dispatcher_cfg.concourse_config_names()
    concourse_cfgs = map(cfg_factory.concourse, concourse_cfg_names)
    failed_hooks = 0

    # define filter function here - it does not change for a given webhook_dispatcher_cfg
    def url_filter(url):
        parsed_url = parse_qs(urlparse(url).query)
        whd_id = parsed_url.get(
            github.webhook.WebhookQueryAttributes.WHD_ID_ATTRIBUTE_NAME
        )
        # consider an url for removal iff it contains parameter
        # 'whd_id' matching given whd_id
        return (
            whd_id is not None and
            webhook_dispatcher_cfg.name() in whd_id
        )

    for concourse_cfg in concourse_cfgs:
        job_mapping_set = cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())

        # We need to gather all the organizations and corresponding Githubs so that we can
        # instantiate the Github api clients with the correct GitHub api cfg.
        github_mapping = process_job_mapping_set(job_mapping_set)

        for github_cfg_name, organizations in github_mapping.items():
            github_api = _create_github_api_object(
                github_cfg=cfg_factory.github(github_cfg_name),
            )

            webhook_syncer = github.webhook.GithubWebHookSyncer(github_api)
            callback_url = build_callback_url(
                ingress_host_url=webhook_dispatcher_deployment_cfg.ingress_host(),
                webhook_dispatcher_cfg_name=webhook_dispatcher_cfg.name(),
            )

            for organization_name in organizations:
                try:
                    webhook_syncer.add_or_update_hook(
                        organization_name=organization_name,
                        callback_url=callback_url,
                        skip_ssl_validation=False,
                    )
                except Exception as e:
                    failed_hooks += 1
                    warning(f'org: {organization_name} - error: {e}')

                removed = webhook_syncer.remove_outdated_hooks(
                    organization_name=organization_name,
                    urls_to_keep=callback_url,
                    # only process webhooks that were created by "us"
                    url_filter_fun=url_filter,
                )
                info(f'Updated hook for "{organization_name}"')
                if removed > 0:
                    info(f'removed {removed} outdated hook(s) from "{organization_name}"')

        if failed_hooks is not 0:
            fail(f'Some webhooks could not be set - for more details see above.')


def process_job_mapping_set(
    job_mapping_set: JobMappingSet,
):
    # Obtain mappings of GitHub instance to organisations for all job mappings in this set and
    # merge them to obtain a single, definitive mapping of
    # (GitHub instance -> orgs on that instance) for all organisations in this JobMappingSet
    result = dict()
    for _, job_mapping in job_mapping_set.job_mappings().items():
        result = merge_dicts(result, create_github_to_org_map(job_mapping))
    return result


def create_github_to_org_map(
    job_mapping: JobMapping,
):
    github_organisation_cfgs = job_mapping.github_organisations()
    # Create a list of (GitHub instance -> org) mappings from the organisation configs.
    github_organisation_mappings = map(
        lambda cfg: {cfg.github_cfg_name(): [cfg.org_name()]},
        github_organisation_cfgs
    )
    # Merge the resulting mappings so that we get all orgs grouped by GitHub instance in _this_
    # mapping
    # TODO: Maybe use reduce, even if its not recommended by G.v.R
    result = dict()
    for mapping in github_organisation_mappings:
        result = merge_dicts(result, mapping)
    return result


def build_callback_url(
    ingress_host_url: str,
    webhook_dispatcher_cfg_name: str,
):
    scheme = 'https'
    netloc = ingress_host_url
    path = 'github-webhook'
    params = ''
    query = '{name}={value}'.format(
        name=github.webhook.WebhookQueryAttributes.WHD_ID_ATTRIBUTE_NAME,
        value=webhook_dispatcher_cfg_name,
    )
    fragment=''

    return urlunparse([scheme, netloc, path, params, query, fragment])
