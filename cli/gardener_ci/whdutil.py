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

import logging

import ci.util
ci.util.ctx().configure_default_logging(stdout_leve=logging.DEBUG)


def start_whd(
    cfg_set_name: str,
    port: int=5000,
    production: bool=False,
    workers: int=4,
):
    import whd.server

    cfg_factory = ci.util.ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_set_name)
    webhook_dispatcher_cfg = cfg_set.webhook_dispatcher()

    app = whd.server.webhook_dispatcher_app(
        cfg_set=cfg_set,
        whd_cfg=webhook_dispatcher_cfg
    )

    # allow external connections
    any_interface = '0.0.0.0'

    if production:
        import bjoern

        def serve():
            bjoern.run(app, any_interface, port, reuse_port=True)
        for _ in range(workers - 1):
            serve()
        serve()
    else:
        import werkzeug.serving
        werkzeug.serving.run_simple(
            hostname=any_interface,
            port=port,
            application=app,
            use_reloader=True,
            use_debugger=True,
        )
