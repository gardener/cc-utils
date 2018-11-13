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

from flask import abort, request

from flask_restful import (
    Resource,
    reqparse,
)

from model.webhook_dispatcher import WebhookDispatcherConfig
from .dispatcher import GithubWebhookDispatcher
from .model import PushEvent, PullRequestEvent


class GithubWebhook(Resource):
    def __init__(self, whd_cfg: WebhookDispatcherConfig):
        self.whd_cfg = whd_cfg
        self.parser = reqparse.RequestParser()
        self.parser.add_argument('X-GitHub-Event', type=str, location='headers')
        self.dispatcher = GithubWebhookDispatcher(whd_cfg=whd_cfg)

    def post(self):
        args = self.parser.parse_args()
        event = args.get('X-GitHub-Event')
        if not event:
            abort(400, 'X-GitHub-Event must be set')

        if event == 'push':
            parsed = PushEvent(raw_dict=request.get_json())
            self.dispatcher.dispatch_push_event(push_event=parsed)
            return 'OK'
        elif event == 'pull_request':
            parsed = PullRequestEvent(raw_dict=request.get_json())
            self.dispatcher.dispatch_pullrequest_event(pr_event=parsed)
        else:
            return f'event {event} ignored'
