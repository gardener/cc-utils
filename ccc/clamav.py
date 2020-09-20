# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import ci.util

from clamav.client import ClamAVClient
from clamav.routes import ClamAVRoutes
from model.clamav import ClamAVConfig


def client_from_config(clamav_config: ClamAVConfig):
    url = clamav_config.service_url()
    routes = ClamAVRoutes(base_url=url)
    return ClamAVClient(routes=routes)


def client_from_config_name(clamav_config_name: str):
    cfg_factory = ci.util.ctx().cfg_factory()
    clamav_config = cfg_factory.clamav(clamav_config_name)
    return client_from_config(clamav_config)
