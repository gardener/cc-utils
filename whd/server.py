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
import logging
import traceback

import falcon

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
        raise falcon.HTTPInternalServerError

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
            es_client=es_client,
        ),
    )
    app.add_error_handler(
        exception=Exception,
        handler=handle_exception,
    )

    return app
