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

import ccc.elasticsearch
from model.webhook_dispatcher import WebhookDispatcherConfig
from .dispatcher import GithubWebhookDispatcher
from .model import CreateEvent, PushEvent, PullRequestEvent

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class GithubWebhook:
    def __init__(
        self,
        cfg_factory,
        cfg_set,
        whd_cfg: WebhookDispatcherConfig,
        es_client: ccc.elasticsearch.ElasticSearchClient,
    ):
        self.dispatcher = GithubWebhookDispatcher(
            cfg_factory=cfg_factory,
            cfg_set=cfg_set,
            whd_cfg=whd_cfg,
        )
        self.es_client = es_client

    def on_post(self, req, resp):
        event = req.get_header('X-GitHub-Event', required=True)
        delivery = req.get_header('X-GitHub-Delivery', required=True)
        logger_string = f'received event (delivery-id: {delivery}) of type "{event}"'
        action = req.media.get("action")
        if action:
            logger_string += f' with action "{action}"'
        repository_name = req.media.get("repository", {}).get("full_name")
        # no GHES header ^= github.com
        hostname = req.get_header('X-GitHub-Enterprise-Host') or 'github.com'
        if repository_name:
            logger_string += f' for repository "{repository_name}"'
        if hostname:
            logger_string += f' from "{hostname}"'

        logger.info(logger_string)
        dispatch_start_time = datetime.datetime.now()
        if event == 'push':
            parsed = PushEvent(raw_dict=req.media, delivery=delivery, hostname=hostname)
            self.dispatcher.dispatch_push_event(
                push_event=parsed,
                es_client=self.es_client,
                delivery_id=delivery,
                hostname=hostname,
                repository=repository_name,
                dispatch_start_time=dispatch_start_time,
            )
            return
        if event == 'create':
            parsed = CreateEvent(raw_dict=req.media, delivery=delivery, hostname=hostname)
            self.dispatcher.dispatch_create_event(
                create_event=parsed,
                es_client=self.es_client,
                delivery_id=delivery,
                hostname=hostname,
                repository=repository_name,
                dispatch_start_time=dispatch_start_time,
            )
            return
        elif event == 'pull_request':
            parsed = PullRequestEvent(raw_dict=req.media, delivery=delivery, hostname=hostname)
            processing = self.dispatcher.dispatch_pullrequest_event(
                pr_event=parsed,
                es_client=self.es_client,
                dispatch_start_time=dispatch_start_time,
            )
            if not processing:
                resp.text = "Event ignored"
            return
        else:
            msg = f'event {event} ignored'
            logger.info(msg)
            return
