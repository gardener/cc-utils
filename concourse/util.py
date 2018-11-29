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

import github.webhook
from github.util import _create_github_api_object

from model.concourse import (
    JobMappingSet,
)
from model.webhook_dispatcher import (
    WebhookDispatcherDeploymentConfig,
)
from util import info, warning, ctx, create_url_from_attributes


def sync_org_webhooks(whd_deployment_cfg: WebhookDispatcherDeploymentConfig,):
    '''Syncs required organization webhooks for a given webhook dispatcher instance'''

    for organization_name, github_api, webhook_url in \
            _enumerate_required_org_webhooks(whd_deployment_cfg=whd_deployment_cfg):

        webhook_syncer = github.webhook.GithubWebHookSyncer(github_api)
        failed_hooks = 0
        try:
            webhook_syncer.create_or_update_org_hook(
                organization_name=organization_name,
                webhook_url=webhook_url,
                skip_ssl_validation=False,
            )
            info(f'Created/updated organization hook for organization "{organization_name}"')
        except Exception as e:
            failed_hooks += 1
            warning(f'org: {organization_name} - error: {e}')

    if failed_hooks is not 0:
        warning('Some webhooks could not be set - for more details see above.')


def _enumerate_required_org_webhooks(
    whd_deployment_cfg: WebhookDispatcherDeploymentConfig,
):
    '''Returns tuples of 'github orgname', 'github api object' and 'webhook url' '''
    cfg_factory = ctx().cfg_factory()

    whd_cfg_name = whd_deployment_cfg.webhook_dispatcher_config_name()
    whd_cfg = cfg_factory.webhook_dispatcher(whd_cfg_name)

    concourse_cfg_names = whd_cfg.concourse_config_names()
    concourse_cfgs = map(cfg_factory.concourse, concourse_cfg_names)

    for concourse_cfg in concourse_cfgs:
        job_mapping_set = cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())

        for github_orgname, github_cfg_name in _enumerate_github_org_configs(job_mapping_set):
            github_api = _create_github_api_object(
                github_cfg=cfg_factory.github(github_cfg_name),
            )

            webhook_url = create_url_from_attributes(
                netloc=whd_deployment_cfg.ingress_host(),
                scheme='https',
                path='github-webhook',
                params='',
                query='{name}={value}'.format(
                    name=github.webhook.DEFAULT_ORG_HOOK_QUERY_KEY,
                    value=whd_cfg_name
                ),
                fragment=''
            )

            yield (github_orgname, github_api, webhook_url)


def _enumerate_github_org_configs(job_mapping_set: JobMappingSet,):
    '''Returns tuples of github org names and github config names'''
    for _, job_mapping in job_mapping_set.job_mappings().items():
        github_org_configs = job_mapping.github_organisations()

        for github_org_config in github_org_configs:
            yield (github_org_config.org_name(), github_org_config.github_cfg_name())
