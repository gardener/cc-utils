# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import typing

import ci.util
import clamav.client
import clamav.routes

from model.clamav import ClamAVConfig
import http_requests


def client(
    cfg: typing.Union[str, ClamAVConfig, None]=None,
    url: str=None,
    cfg_factory=None,
):
    if not (bool(cfg) ^ bool(url)):
        raise ValueError('exactly one of cfg, url must be passed')

    if isinstance(cfg, ClamAVConfig):
        url = cfg.service_url()
    elif isinstance(cfg, str):
        if not cfg_factory:
            cfg_factory = ci.util.ctx().cfg_factory()
        cfg = cfg_factory.clamav(cfg)
        url = cfg.service_url()
    elif cfg is None:
        url = url

    routes = clamav.routes.ClamAVRoutes(base_url=url)
    retry_cfg = http_requests.LoggingRetry(
        total=8,
        connect=8,
        read=8,
        backoff_factor=2.0,
        allowed_methods=('POST', 'GET'),
    )

    client = clamav.client.ClamAVClient(
        routes=routes,
        retry_cfg=retry_cfg,
    )

    return client
