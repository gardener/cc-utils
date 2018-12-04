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

from gevent.pywsgi import WSGIServer

import whd.server as whd_server
import util


def start_whd(
    webhook_dispatcher_cfg_name: str='sap_external',
    port: int=5000,
    production: bool=False,
):
    cfg_factory = util.ctx().cfg_factory()
    webhook_dispatcher_cfg = cfg_factory.webhook_dispatcher(webhook_dispatcher_cfg_name)

    app = whd_server.webhook_dispatcher_app(whd_cfg=webhook_dispatcher_cfg)

    if production:
        server = WSGIServer(('0.0.0.0', port), app, log = None)
        server.serve_forever()
    else:
        app.run(debug=True, port=port, host='0.0.0.0')
