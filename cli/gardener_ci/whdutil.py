# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import logging
import multiprocessing

import ci.util
ci.util.ctx().configure_default_logging(stdout_level=logging.INFO)


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
        multiprocessing.set_start_method('fork')

        def serve():
            bjoern.run(app, any_interface, port, reuse_port=True)
        for _ in range(workers - 1):
            p = multiprocessing.Process(target=serve)
            p.start()
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
