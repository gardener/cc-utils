# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import logging
import traceback

import falcon # pylint: disable=E0401

from .webhook import GithubWebhook
from model.webhook_dispatcher import WebhookDispatcherConfig


logger = logging.getLogger(__name__)


def webhook_dispatcher_app(
    cfg_factory,
    cfg_set,
    whd_cfg: WebhookDispatcherConfig,
):
    def handle_exception(ex, req, resp, params):
        exc_trace = traceback.format_exc()
        logger.error(exc_trace)
        # raise HTTP error to not leak logs to client
        raise falcon.HTTPInternalServerError # noqa

    app = falcon.App(
        middleware=[],
    )

    app.add_route('/github-webhook',
        GithubWebhook(
            cfg_factory=cfg_factory,
            whd_cfg=whd_cfg,
            cfg_set=cfg_set,
        ),
    )
    app.add_error_handler(
        exception=Exception,
        handler=handle_exception,
    )

    return app
