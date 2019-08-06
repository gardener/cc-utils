# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

import bjoern

import whd.server as whd_server
import util


def start_whd(
    cfg_set_name: str,
    webhook_dispatcher_cfg_name: str='sap_external',
    port: int=5000,
    production: bool=False,
):
    cfg_factory = util.ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_set_name)
    webhook_dispatcher_cfg = cfg_factory.webhook_dispatcher(webhook_dispatcher_cfg_name)

    app = whd_server.webhook_dispatcher_app(
        cfg_set=cfg_set,
        whd_cfg=webhook_dispatcher_cfg
    )

    # allow external connections
    any_interface = '0.0.0.0'

    if production:
        bjoern.run(app, any_interface, port)
    else:
        app.run(debug=True, port=port, host=any_interface)
