# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import falcon

from .webhook import GithubWebhook
from model.webhook_dispatcher import WebhookDispatcherConfig


def webhook_dispatcher_app(
    cfg_set,
    whd_cfg: WebhookDispatcherConfig,
):
    app = falcon.API(
        middleware=[],
    )

    app.add_route('/github-webhook',
        GithubWebhook(
            whd_cfg=whd_cfg,
            cfg_set=cfg_set,
        ),
    )

    return app
