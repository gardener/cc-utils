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

import functools

from model.webhook_dispatcher import WebhookDispatcherConfig
import concourse.client
import util


class GithubWebhookDispatcher(object):
    def __init__(
        self,
        whd_cfg: WebhookDispatcherConfig
    ):
        self.whd_cfg = whd_cfg
        self.cfg_factory = util.ctx().cfg_factory()

    @functools.lru_cache()
    def concourse_clients(self):
        for concourse_config_name in self.whd_cfg.concourse_config_names():
            concourse_cfg = self.cfg_factory.concourse(concourse_config_name)
            job_mapping_set = self.cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())
            for job_mapping in job_mapping_set.job_mappings().values():
                yield concourse.client.from_cfg(
                    concourse_cfg=concourse_cfg,
                    team_name=job_mapping.team_name(),
                )

    def dispatch_push_event(self, push_event):
        for concourse_api in self.concourse_clients():
            resources = self._matching_resources(
                concourse_api=concourse_api,
                push_event=push_event,
            )
            self._trigger_resource_check(concourse_api=concourse_api, resources=resources)

    def _trigger_resource_check(self, concourse_api, resources):
        for resource in resources:
            util.info('triggering resource check for: ' + resource.name)
            concourse_api.trigger_resource_check(
                pipeline_name=resource.pipeline_name(),
                resource_name=resource.name,
            )

    def _matching_resources(self, concourse_api, push_event):
        resources = concourse_api.pipeline_resources(concourse_api.pipelines())
        for resource in resources:
            if not resource.has_webhook_token():
                continue
            if not resource.type == 'git':
                continue
            ghs = resource.github_source()
            if not ghs.hostname() == push_event.github_host():
                continue
            if not ghs.repo_path().lstrip('/') == push_event.repository_path():
                continue
            if not push_event.ref().endswith(ghs.branch_name()):
                continue

            yield resource
