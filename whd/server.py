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

import falcon

from .webhook import GithubWebhook
from model.webhook_dispatcher import WebhookDispatcherConfig


def webhook_dispatcher_app(
    cfg_factory,
    cfg_set,
    whd_cfg: WebhookDispatcherConfig,
):

    # falcon.API will be removed with falcon 4.0.0
    # see: https://github.com/falconry/falcon/
    # blob/a5e72b287efb2b3da632cf6547ed3f07d8ec5380/falcon/app.py#L1058
    if app := getattr(falcon, 'App', None):
        app = app(
            middleware=[],
        )
    else:
        app = falcon.API(
            middleware=[],
        )

    app.add_route('/github-webhook',
        GithubWebhook(
            cfg_factory=cfg_factory,
            whd_cfg=whd_cfg,
            cfg_set=cfg_set,
        ),
    )

    return app
