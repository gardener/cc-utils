# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import logging
import traceback

import falcon # pylint: disable=E0401

import ccc.elasticsearch
from .webhook import GithubWebhook
from model.webhook_dispatcher import WebhookDispatcherConfig
import whd.metric


logger = logging.getLogger(__name__)


def webhook_dispatcher_app(
    cfg_factory,
    cfg_set,
    whd_cfg: WebhookDispatcherConfig,
):
    es_client = ccc.elasticsearch.from_cfg(cfg_set.elasticsearch())
    if es_client:
        logger.info('will write webhook metrics to ES')

    def handle_exception(ex, req, resp, params):
        if not es_client:
            raise falcon.HTTPInternalServerError
        exc_trace = traceback.format_exc()
        logger.error(exc_trace)
        req_body = req.media
        exception_metric = whd.metric.ExceptionMetric.create(
            service='whd',
            stacktrace=exc_trace,
            request=req_body,
            params=params,
        )
        ccc.elasticsearch.metric_to_es(
            es_client=es_client,
            metric=exception_metric,
            index_name=whd.metric.index_name(exception_metric),
        )
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
            es_client=es_client,
        ),
    )
    app.add_error_handler(
        exception=Exception,
        handler=handle_exception,
    )

    return app
