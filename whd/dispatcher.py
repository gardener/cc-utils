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
import logging
import threading
import typing

import requests

import ccc.concourse
import ccc.elasticsearch
import ccc.github
import ccc.secrets_server
import ci.util
import concourse.client.api
import concourse.client.model
import concourse.enumerator
import concourse.replicator
import model
import whd.model
import whd.pull_request
import whd.util

from github3.exceptions import NotFoundError

from .pipelines import replicate_repository_pipelines
from concourse.client.util import determine_jobs_to_be_triggered
from concourse.enumerator import JobMappingNotFoundError
from concourse.model.job import AbortObsoleteJobs
from model import ConfigFactory
from model.base import ConfigElementNotFoundError
from model.webhook_dispatcher import WebhookDispatcherConfig

from concourse.client.model import (
    ResourceType,
)
from .model import (
    AbortConfig,
    Pipeline,
    PullRequestAction,
    PullRequestEvent,
    PushEvent,
    RefType,
)
import whd.metric


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class GithubWebhookDispatcher:
    def __init__(
        self,
        cfg_factory,
        cfg_set,
        whd_cfg: WebhookDispatcherConfig
    ):
        self.cfg_factory: model.ConfigFactory = cfg_factory
        self.cfg_set = cfg_set
        self.whd_cfg = whd_cfg
        logger.info(f'github-whd initialised for cfg-set: {cfg_set.name()}')

    def concourse_clients(
        self,
    ) -> typing.Generator[concourse.client.api.ConcourseApiBase, None, None]:
        for concourse_config_name in self.whd_cfg.concourse_config_names():
            concourse_cfg = self.cfg_factory.concourse(concourse_config_name)
            job_mapping_set = self.cfg_factory.job_mapping(concourse_cfg.job_mapping_cfg_name())
            for job_mapping in job_mapping_set.job_mappings().values():
                yield ccc.concourse.client_from_cfg_name(
                    concourse_cfg_name=concourse_cfg.name(),
                    team_name=job_mapping.team_name(),
                )

    def dispatch_create_event(
        self,
        create_event,
        delivery_id: str,
        repository: str,
        hostname: str,
        es_client: ccc.elasticsearch.ElasticSearchClient,
        dispatch_start_time: datetime.datetime,
    ):
        ref_type = create_event.ref_type()
        if not ref_type == RefType.BRANCH:
            logger.info(f'ignored create event with type {ref_type}')
            return

        # todo: rename parameter
        self._update_pipeline_definition(
            push_event=create_event,
            delivery_id=delivery_id,
            repository=repository,
            hostname=hostname,
            es_client=es_client,
            dispatch_start_time=dispatch_start_time,
        )

    def dispatch_push_event(
        self,
        push_event,
        delivery_id: str,
        repository: str,
        hostname: str,
        es_client: ccc.elasticsearch.ElasticSearchClient,
        dispatch_start_time: datetime.datetime,
    ):
        if self._pipeline_definition_changed(push_event):
            try:
                self._update_pipeline_definition(
                    push_event=push_event,
                    delivery_id=delivery_id,
                    repository=repository,
                    hostname=hostname,
                    es_client=es_client,
                    dispatch_start_time=dispatch_start_time,
                )
            except ValueError as e:
                logger.warning(
                    f'Received error updating pipeline-definitions: "{e}". '
                    'Will still abort running jobs (if configured) and trigger resource checks.'
                )

        self.abort_running_jobs_if_configured(push_event)

        def _check_resources(**kwargs):
            for concourse_api in self.concourse_clients():
                logger.debug(f'using concourse-api: {concourse_api}')
                resources = self._matching_resources(
                    concourse_api=concourse_api,
                    event=push_event,
                )
                logger.debug('triggering resource-check')
                whd.util.trigger_resource_check(concourse_api=concourse_api, resources=resources)

            process_end_time = datetime.datetime.now()
            process_total_seconds = (
                process_end_time - kwargs.get('dispatch_start_time')
            ).total_seconds()
            webhook_delivery_metric = whd.metric.WebhookDelivery.create(
                delivery_id=kwargs.get('delivery_id'),
                event_type=kwargs.get('event_type'),
                repository=kwargs.get('repository'),
                hostname=kwargs.get('hostname'),
                process_total_seconds=process_total_seconds,
            )
            if (es_client := kwargs.get('es_client')):
                ccc.elasticsearch.metric_to_es(
                    es_client=es_client,
                    metric=webhook_delivery_metric,
                    index_name=whd.metric.index_name(webhook_delivery_metric),
                )

        thread = threading.Thread(
            target=_check_resources,
            kwargs={
                'delivery_id': delivery_id,
                'hostname': hostname,
                'es_client': es_client,
                'repository': repository,
                'event_type': 'push',
                'dispatch_start_time': dispatch_start_time,
            }
        )
        thread.start()

    def _update_pipeline_definition(
        self,
        push_event,
        delivery_id: str,
        repository: str,
        hostname: str,
        es_client: ccc.elasticsearch.ElasticSearchClient,
        dispatch_start_time: datetime.datetime,
    ):
        def _do_update(
            delivery_id: str,
            event_type: str,
            repository: str,
            hostname: str,
            dispatch_start_time: datetime.datetime,
            es_client: ccc.elasticsearch.ElasticSearchClient,
        ):
            repo_url = push_event.repository().repository_url()
            job_mapping_set = self.cfg_set.job_mapping()
            job_mapping = job_mapping_set.job_mapping_for_repo_url(repo_url, self.cfg_set)

            replicate_repository_pipelines(
                repo_url=repo_url,
                cfg_set=self.cfg_factory.cfg_set(job_mapping.replication_ctx_cfg_set()),
                whd_cfg=self.whd_cfg,
            )

            process_end_time = datetime.datetime.now()
            process_total_seconds = (process_end_time - dispatch_start_time).total_seconds()
            webhook_delivery_metric = whd.metric.WebhookDelivery.create(
                delivery_id=delivery_id,
                event_type=event_type,
                repository=repository,
                hostname=hostname,
                process_total_seconds=process_total_seconds,
            )
            if es_client:
                ccc.elasticsearch.metric_to_es(
                    es_client=es_client,
                    metric=webhook_delivery_metric,
                    index_name=whd.metric.index_name(webhook_delivery_metric),
                )

        try:
            _do_update(
                delivery_id=delivery_id,
                event_type='create',
                repository=repository,
                hostname=hostname,
                dispatch_start_time=dispatch_start_time,
                es_client=es_client,
            )
        except (JobMappingNotFoundError, ConfigElementNotFoundError) as e:
            # A config element was missing or o JobMapping for the given repository was present.
            # Print warning, reload and try again
            logger.warning(
                f'failed to update pipeline definition: {e}. Will reload config and try again.'
            )
            # Attempt to fetch latest cfg from SS and replace it
            raw_dict = ccc.secrets_server.SecretsServerClient.default().retrieve_secrets()
            self.cfg_factory = ConfigFactory.from_dict(raw_dict)
            self.cfg_set = self.cfg_factory.cfg_set(self.cfg_set.name())
            # retry
            _do_update(
                delivery_id=delivery_id,
                event_type='create',
                repository=repository,
                hostname=hostname,
                dispatch_start_time=dispatch_start_time,
                es_client=es_client,
            )

    def _pipeline_definition_changed(self, push_event):
        if '.ci/pipeline_definitions' in push_event.modified_paths():
            return True
        return False

    def determine_affected_pipelines(self, push_event) -> typing.Generator[Pipeline, None, None]:
        '''yield each concourse pipeline that may be affected by the given push-event.
        '''
        repo = push_event.repository()
        repo_url = repo.repository_url()
        job_mapping_set = self.cfg_set.job_mapping()

        try:
            job_mapping = job_mapping_set.job_mapping_for_repo_url(repo_url, self.cfg_set)
        except ValueError:
            logger.info(f'no job-mapping found for {repo_url=} - will not interact w/ pipeline(s)')
            return

        try:
            repo_enumerator = concourse.enumerator.GithubRepositoryDefinitionEnumerator(
                repository_url=repo_url,
                cfg_set=self.cfg_factory.cfg_set(job_mapping.replication_ctx_cfg_set()),
            )
        except concourse.enumerator.JobMappingNotFoundError:
            logger.info(f'no job-mapping matched for {repo_url=} - will not interact w/ pipeline(s)')
            return

        try:
            definition_descriptors = [d for d in repo_enumerator.enumerate_definition_descriptors()]
        except NotFoundError:
            logger.warning(
                f"Unable to access repository '{repo_url}' on github '{repo.github_host()}'. "
                "Please make sure the repository exists and the technical user has the necessary "
                "permissions to access it."
            )
            definition_descriptors = []

        for descriptor in definition_descriptors:
            # need to merge and consider the effective definition
            effective_definition = descriptor.pipeline_definition
            for override in descriptor.override_definitions:
                effective_definition = ci.util.merge_dicts(effective_definition, override)

            yield Pipeline(
                pipeline_name=descriptor.effective_pipeline_name(),
                target_team=descriptor.concourse_target_team,
                effective_definition=effective_definition,
            )

    def matching_client(self, team):
        for c in self.concourse_clients():
            if c.routes.team == team:
                return c

    def abort_running_jobs_if_configured(self, push_event):
        builds_to_consider = 5
        for pipeline in self.determine_affected_pipelines(
            push_event
        ):
            if not (client := self.matching_client(pipeline.target_team)):
                logger.info(
                    f'no matching job-mapping for {pipeline.pipeline_name=} - skipping abortion'
                )
                continue

            try:
                pipeline_config = client.pipeline_cfg(pipeline.pipeline_name)
            except requests.exceptions.HTTPError as e:
                # might not exist yet if the pipeline was just rendered by the WHD
                if e.response.status_code is not requests.status_codes.codes.NOT_FOUND:
                    raise e
                logger.warning(f"could not retrieve pipeline config for '{pipeline.pipeline_name}'")
                return

            resources = [
                r for r in pipeline_config.resources
                if ResourceType(r.type) in (ResourceType.GIT, ResourceType.PULL_REQUEST)
            ]
            for job in determine_jobs_to_be_triggered(*resources):
                if (
                    not pipeline.effective_definition['jobs'].get(job.name)
                    or not 'abort_outdated_jobs' in pipeline.effective_definition['jobs'][job.name]
                ):
                    continue
                abort_cfg = AbortConfig.from_dict(
                    pipeline.effective_definition['jobs'][job.name]
                )

                if abort_cfg.abort_obsolete_jobs is AbortObsoleteJobs.NEVER:
                    continue
                elif (
                    abort_cfg.abort_obsolete_jobs is AbortObsoleteJobs.ON_FORCE_PUSH_ONLY
                    and not push_event.is_forced_push()
                ):
                    continue
                elif abort_cfg.abort_obsolete_jobs is AbortObsoleteJobs.ALWAYS:
                    pass
                else:
                    raise NotImplementedError(abort_cfg.abort_obsolete_jobs)

                running_builds = [
                    b for b in client.job_builds(pipeline.pipeline_name, job.name)
                    if b.status() is concourse.client.model.BuildStatus.RUNNING
                ][:builds_to_consider]

                for build in running_builds:
                    if build.plan().contains_version_ref(push_event.previous_ref()):
                        logger.info(
                            f"Aborting obsolete build '{build.build_number()}' for job '{job.name}'"
                        )
                        client.abort_build(build.id())

    def dispatch_pullrequest_event(
        self,
        pr_event: whd.model.PullRequestEvent,
        es_client: ccc.elasticsearch.ElasticSearchClient,
        dispatch_start_time: datetime.datetime,
    ) -> bool:
        '''Process the given push event.

        Return `True` if event will be processed, `False` if no processing will be done.
        '''
        if not pr_event.action() in (
            PullRequestAction.OPENED,
            PullRequestAction.REOPENED,
            PullRequestAction.LABELED,
            PullRequestAction.SYNCHRONIZE,
        ):
            logger.info(f'ignoring pull-request action {pr_event.action()}')
            return False

        thread = threading.Thread(
            target=whd.pull_request.process_pr_event,
            kwargs={
                'concourse_clients': self.concourse_clients(),
                'cfg_factory': self.cfg_factory,
                'whd_cfg': self.whd_cfg,
                'cfg_set': self.cfg_set,
                'pr_event': pr_event,
                'es_client': es_client,
                'dispatch_start_time': dispatch_start_time,
            }
        )
        thread.start()

        return True

    def _matching_resources(
        self,
        concourse_api: concourse.client.api.ConcourseApiBase,
        event,
    ) -> typing.Generator[concourse.client.model.PipelineConfigResource, None, None]:
        if isinstance(event, PushEvent):
            resource_type = ResourceType.GIT
        elif isinstance(event, PullRequestEvent):
            resource_type = ResourceType.PULL_REQUEST
        else:
            raise NotImplementedError

        resources_gen = concourse_api.pipeline_resources(
            concourse_api.pipelines(),
            resource_type=resource_type,
        )

        for resource in resources_gen:
            resource: concourse.client.model.PipelineConfigResource
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
