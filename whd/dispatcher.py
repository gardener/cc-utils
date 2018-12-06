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
import time

from flask import current_app as app
from model.webhook_dispatcher import WebhookDispatcherConfig
from .model import PushEvent, PullRequestEvent, PullRequestAction
import concourse.client
import util


class GithubWebhookDispatcher(object):
    def __init__(
        self,
        cfg_set,
        whd_cfg: WebhookDispatcherConfig
    ):
        self.cfg_set = cfg_set
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
        if self._pipeline_definition_changed(push_event):
            self._update_pipeline_definition(push_event)

        for concourse_api in self.concourse_clients():
            resources = self._matching_resources(
                concourse_api=concourse_api,
                event=push_event,
            )
            self._trigger_resource_check(concourse_api=concourse_api, resources=resources)

    def _update_pipeline_definition(self, push_event):
        # for now, just log - actual update to be implemented
        app.logger.info('pipeline definition update found - should now update')

    def _pipeline_definition_changed(self, push_event):
        if '.ci/pipeline_definitions' in push_event.modified_paths():
            return True
        return False

    def dispatch_pullrequest_event(self, pr_event):
        if not pr_event.action() in (
            PullRequestAction.OPENED,
            PullRequestAction.REOPENED,
            PullRequestAction.LABELED,
            PullRequestAction.SYNCHRONIZE,
        ):
            return app.logger.info(f'ignoring pull-request action {pr_event.action()}')

        for concourse_api in self.concourse_clients():
            resources = list(self._matching_resources(
                concourse_api=concourse_api,
                event=pr_event,
            ))
            self._trigger_resource_check(concourse_api=concourse_api, resources=resources)
            self._ensure_pr_resource_updates(
                concourse_api=concourse_api,
                pr_event=pr_event,
                resources=resources,
            )

    def _trigger_resource_check(self, concourse_api, resources):
        for resource in resources:
            app.logger.info('triggering resource check for: ' + resource.name)
            concourse_api.trigger_resource_check(
                pipeline_name=resource.pipeline_name(),
                resource_name=resource.name,
            )

    def _matching_resources(self, concourse_api, event):
        resources = concourse_api.pipeline_resources(concourse_api.pipelines())
        if isinstance(event, PushEvent):
            resource_type = 'git'
        elif isinstance(event, PullRequestEvent):
            resource_type = 'pull-request'
        else:
            raise NotImplementedError

        for resource in resources:
            if not resource.has_webhook_token():
                continue
            if not resource.type == resource_type:
                continue
            ghs = resource.github_source()
            repository = event.repository()
            if not ghs.hostname() == repository.github_host():
                continue
            if not ghs.repo_path().lstrip('/') == repository.repository_path():
                continue
            if isinstance(event, PushEvent):
                if not event.ref().endswith(ghs.branch_name()):
                    continue

            yield resource

    def _ensure_pr_resource_updates(
        self,
        concourse_api,
        pr_event,
        resources,
        retries=10,
        sleep_seconds=0,
    ):
        time.sleep(sleep_seconds)
        if sleep_seconds == 0:
            sleep_seconds = 3

        retries -= 1
        if retries < 0:
            app.logger.info('giving up')
            return

        def resource_versions(resource):
            return concourse_api.resource_versions(
                pipeline_name=resource.pipeline_name(),
                resource_name=resource.name,
            )

        def is_up_to_date(resource, resource_versions):
            # check if pr requires a label to be present
            require_label = resource.source.get('label')
            if require_label:
                if require_label not in pr_event.label_names():
                    app.logger.info('skipping PR resource update (required label not present)')
                    # regardless of whether or not the resource is up-to-date, it would not
                    # be discovered by concourse's PR resource due to policy
                    return True

            # assumption: PR resource is up-to-date if our PR-number is listed
            # XXX hard-code structure of concourse-PR-resource's version dict
            pr_numbers = map(lambda r: r.version()['pr'], resource_versions)

            return str(pr_event.number()) in pr_numbers

        # filter out all resources that are _not_ up-to-date (we only care about those)
        outdated_resources = [
            resource for resource in resources
            if not is_up_to_date(resource, resource_versions(resource))
        ]

        if not outdated_resources:
            app.logger.info('no outdated_resources PR found')
            return # nothing to do

        app.logger.info(f'found {len(outdated_resources)} PR resource(s) that require being updated')
        self._trigger_resource_check(concourse_api=concourse_api, resources=outdated_resources)
        app.logger.info(f'retriggered resource check will try again {retries} more times')

        self._ensure_pr_resource_updates(
            concourse_api=concourse_api,
            pr_event=pr_event,
            resources=outdated_resources,
            retries=retries,
            sleep_seconds=sleep_seconds*1.2,
        )
