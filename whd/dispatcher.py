# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import datetime
import functools
import logging
import random
import threading
import time
import traceback

import ccc.elasticsearch
import ccc.secrets_server
import ci.util
import concourse.client

from .pipelines import update_repository_pipelines
from concourse.enumerator import JobMappingNotFoundError
from github.util import GitHubRepositoryHelper
from model import ConfigFactory
from model.webhook_dispatcher import WebhookDispatcherConfig

from concourse.client.util import (
    jobs_not_triggered,
    pin_resource_and_trigger_build,
    PinningFailedError,
    PinningUnnecessary,
)
from concourse.client.model import (
    ResourceType,
)
from .model import (
    PushEvent,
    PullRequestEvent,
    PullRequestAction,
    RefType,
)


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class GithubWebhookDispatcher(object):
    def __init__(
        self,
        cfg_set,
        whd_cfg: WebhookDispatcherConfig
    ):
        self.cfg_set = cfg_set
        self.whd_cfg = whd_cfg
        self.cfg_factory = ci.util.ctx().cfg_factory()
        logger.info(f'github-whd initialised for cfg-set: {cfg_set.name()}')

    def concourse_clients(self):
        for concourse_config_name in self.whd_cfg.concourse_config_names():
            concourse_cfg = self.cfg_factory.concourse(concourse_config_name)
            job_mapping_set = self.cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())
            for job_mapping in job_mapping_set.job_mappings().values():
                yield concourse.client.from_cfg(
                    concourse_cfg=concourse_cfg,
                    team_name=job_mapping.team_name(),
                )

    def dispatch_create_event(self, create_event):
        ref_type = create_event.ref_type()
        if not ref_type == RefType.BRANCH:
            logger.info(f'ignored create event with type {ref_type}')
            return

        # todo: rename parameter
        self._update_pipeline_definition(push_event=create_event)

    def dispatch_push_event(self, push_event):
        if self._pipeline_definition_changed(push_event):
            self._update_pipeline_definition(push_event)

        logger.debug('before push-event dispatching')

        def _check_resources():
            for concourse_api in self.concourse_clients():
                logger.debug(f'using concourse-api: {concourse_api}')
                resources = self._matching_resources(
                    concourse_api=concourse_api,
                    event=push_event,
                )
                logger.debug('triggering resource-check')
                self._trigger_resource_check(concourse_api=concourse_api, resources=resources)

        thread = threading.Thread(target=_check_resources)
        thread.start()

    def _update_pipeline_definition(self, push_event):
        try:
            try:
                update_repository_pipelines(
                    repo_url=push_event.repository().repository_url(),
                    cfg_set=self.cfg_set,
                    whd_cfg=self.whd_cfg,
                )
            except JobMappingNotFoundError as je:
                # No JobMapping for the given repository was present. Print warning, reload and try
                # again
                logger.warning(
                    f'failed to update pipeline definition: {je}. Will reload config and try again.'
                )
                # Attempt to fetch latest cfg from SS and replace it
                raw_dict = ccc.secrets_server.SecretsServerClient.default().retrieve_secrets()
                factory = ConfigFactory.from_dict(raw_dict)
                self.cfg_set = factory.cfg_set(self.cfg_set.name())
                # retry
                update_repository_pipelines(
                    repo_url=push_event.repository().repository_url(),
                    cfg_set=self.cfg_set,
                    whd_cfg=self.whd_cfg,
                )
        except BaseException as be:
            logger.warning(f'failed to update pipeline definition - ignored {be}')
            import traceback
            try:
                traceback.print_exc()
            except BaseException:
                pass # ignore

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
            return logger.info(f'ignoring pull-request action {pr_event.action()}')

        def _process_pr_event():
            for concourse_api in self.concourse_clients():
                resources = list(self._matching_resources(
                    concourse_api=concourse_api,
                    event=pr_event,
                ))

                if len(resources) == 0:
                    continue

                if pr_event.action() in [PullRequestAction.OPENED, PullRequestAction.SYNCHRONIZE]:
                    self._set_pr_labels(pr_event, resources)

                logger.info(f'triggering resource check for PR number: {pr_event.number()}')
                self._trigger_resource_check(concourse_api=concourse_api, resources=resources)
                self._ensure_pr_resource_updates(
                    concourse_api=concourse_api,
                    pr_event=pr_event,
                    resources=resources,
                )
                # Give concourse a chance to react
                time.sleep(random.randint(5,10))
                self.handle_untriggered_jobs(pr_event=pr_event, concourse_api=concourse_api)

        thread = threading.Thread(target=_process_pr_event)
        thread.start()

    def _trigger_resource_check(self, concourse_api, resources):
        logger.debug('_trigger_resource_check')
        for resource in resources:
            logger.info('triggering resource check for: ' + resource.name)
            try:
                concourse_api.trigger_resource_check(
                    pipeline_name=resource.pipeline_name(),
                    resource_name=resource.name,
                )
            except Exception:
                traceback.print_exc()

    def _set_pr_labels(self, pr_event, resources) -> bool:
        '''
        @ return True if the required label was set
        '''
        required_labels = {
            resource.source.get('label')
            for resource in resources if resource.source.get('label') is not None
        }
        if not required_labels:
            return False
        repo = pr_event.repository()
        repository_path = repo.repository_path()
        pr_number = pr_event.number()

        github_cfg = self.cfg_set.github()
        owner, name = repository_path.split('/')
        github_helper = GitHubRepositoryHelper(
            owner,
            name,
            github_cfg=github_cfg,
        )
        sender_login = pr_event.sender()['login']
        if pr_event.action() is PullRequestAction.OPENED:
            if github_helper.is_pr_created_by_org_member(pr_number):
                logger.info(
                    f"New pull request by member of '{owner}' in '{repository_path}' found. "
                    f"Setting required labels '{required_labels}'."
                )
                github_helper.add_labels_to_pull_request(pr_number, *required_labels)
                return True
            else:
                logger.debug(
                    f"New pull request by member in '{repository_path}' found, but creator is not "
                    f"member of '{owner}' - will not set required labels."
                )
                github_helper.add_comment_to_pr(
                    pull_request_number=pr_number,
                    comment=(
                        f"Thank you @{sender_login} for your contribution. Before I can start "
                        "building your PR, a member of the organization must set the required "
                        f"label(s) {required_labels}. Once started, you can check the build "
                        "status in the PR checks section below."
                    )
                )
                return False
        elif pr_event.action() is PullRequestAction.SYNCHRONIZE:
            if github_helper.is_org_member(organization_name=owner, user_login=sender_login):
                logger.info(
                    f"Update to pull request #{pr_event.number()} by org member '{sender_login}' "
                    f" in '{repository_path}' found. Setting required labels '{required_labels}'."
                )
                github_helper.add_labels_to_pull_request(pr_number, *required_labels)
                return True
            else:
                logger.debug(
                    f"Update to pull request #{pr_event.number()} by '{sender_login}' "
                    f" in '{repository_path}' found. Ignoring, since they are not an org member'."
                )
                return False
        return False

    def _matching_resources(self, concourse_api, event):
        if isinstance(event, PushEvent):
            resource_type = ResourceType.GIT
        elif isinstance(event, PullRequestEvent):
            resource_type = ResourceType.PULL_REQUEST
        else:
            raise NotImplementedError

        resources = concourse_api.pipeline_resources(
            concourse_api.pipelines(),
            resource_type=resource_type,
        )

        for resource in resources:
            if not resource.has_webhook_token():
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
            if isinstance(event, PushEvent):
                if msg := event.commit_message():
                    if (
                        not ghs.disable_ci_skip()
                        and any(skip in msg for skip in ('[skip ci]', '[ci skip]'))
                    ):
                        logger.info(
                            f"Do not trigger resource {resource.name}. Found [skip ci] or [ci skip]"
                        )
                        continue

            yield resource

    def _ensure_pr_resource_updates(
        self,
        concourse_api,
        pr_event,
        resources,
        retries=10,
        sleep_seconds=3,
    ):
        time.sleep(sleep_seconds)

        retries -= 1
        if retries < 0:
            try:
                self.log_outdated_resources(resources)
            # ignore logging errors
            except BaseException:
                pass

            outdated_resources_names = [r.name for r in resources]
            logger.info(f'could not update resources {outdated_resources_names} - giving up')
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
                    logger.info('skipping PR resource update (required label not present)')
                    # regardless of whether or not the resource is up-to-date, it would not
                    # be discovered by concourse's PR resource due to policy
                    return True

            # assumption: PR resource is up-to-date if our PR-number is listed
            # XXX hard-code structure of concourse-PR-resource's version dict
            pr_numbers = map(lambda r: r.version()['pr'], resource_versions)

            return str(pr_event.number()) in pr_numbers

        # filter out all resources that are _not_ up-to-date (we only care about those).
        # Also keep resources that currently fail to check so that we keep retrying those
        outdated_resources = [
            resource for resource in resources
            if resource.failing_to_check()
            or not is_up_to_date(resource, resource_versions(resource))
        ]

        if not outdated_resources:
            logger.info('no outdated PR resources found')
            return # nothing to do

        logger.info(f'found {len(outdated_resources)} PR resource(s) that require being updated')
        self._trigger_resource_check(concourse_api=concourse_api, resources=outdated_resources)
        logger.info(f'retriggered resource check will try again {retries} more times')

        self._ensure_pr_resource_updates(
            concourse_api=concourse_api,
            pr_event=pr_event,
            resources=outdated_resources,
            retries=retries,
            sleep_seconds=sleep_seconds*1.2,
        )

    def handle_untriggered_jobs(self, pr_event: PullRequestEvent, concourse_api):
        for job, resource, resource_version in jobs_not_triggered(pr_event, concourse_api):
            logger.info(
                f'processing untriggered job {job.name=} of {resource.pipeline_name()=} '
                f'{resource.name=} {resource_version.version()=}. Triggered by {pr_event.action()=}'
            )
            try:
                pin_resource_and_trigger_build(
                    job=job,
                    resource=resource,
                    resource_version=resource_version,
                    concourse_api=concourse_api,
                    retries=3,
                )
            except PinningUnnecessary as e:
                logger.info(e)
            except PinningFailedError as e:
                logger.warning(e)

    @functools.lru_cache()
    def els_client(self):
        elastic_cfg = self.cfg_set.elasticsearch()
        elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)
        return elastic_client

    def log_outdated_resources(self, outdated_resources):
        els_index = self.cfg_set.webhook_dispatcher_deployment().logging_els_index()
        elastic_client = self.els_client()

        date = datetime.datetime.utcnow().isoformat()
        elastic_client.store_documents(
            index=els_index,
            body=[
                {
                    'date': date,
                    'resource_name': resource.name,
                    'pipeline_name': resource.pipeline_name(),
                }
                for resource in outdated_resources
            ],
        )
