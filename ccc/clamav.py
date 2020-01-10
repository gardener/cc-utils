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
