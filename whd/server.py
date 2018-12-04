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

import logging
from flask import Flask
from flask_restful import Api

from .webhook import GithubWebhook
from model.webhook_dispatcher import WebhookDispatcherConfig


def webhook_dispatcher_app(whd_cfg: WebhookDispatcherConfig):
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)
    api = Api(app)

    api.add_resource(
        GithubWebhook,
        '/github-webhook',
        resource_class_kwargs={
            'whd_cfg': whd_cfg,
        }
    )

    return app
