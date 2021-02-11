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

import argparse
import logging

import uvicorn

import ci.util


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "uvicorn": {"level": logging.INFO},
        "uvicorn.error": {"level": logging.INFO},
        "uvicorn.access": {"level": logging.INFO},
    },
}


def app():
    import whd.server

    parser = argparse.ArgumentParser()
    parser.add_argument('--cfg-set-name', action='store', dest='cfg_set_name', type=str)
    args, _ = parser.parse_known_args()

    cfg_factory = ci.util.ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(args.cfg_set_name)
    webhook_dispatcher_cfg = cfg_set.webhook_dispatcher()

    app = whd.server.webhook_dispatcher_app(
        cfg_set=cfg_set,
        whd_cfg=webhook_dispatcher_cfg
    )
    return app


def _logging_config_dict(stdout_level):
    return {
     "version": 1,
        "disable_existing_loggers": False,
        "loggers": {
            "uvicorn": {"level": stdout_level},
            "uvicorn.error": {"level": stdout_level},
            "uvicorn.access": {"level": stdout_level},
        },
    }


def start_whd(
    cfg_set_name: str,
    port: int=5000,
    production: bool=False,
    workers: int=4,
):
    import whd
    whd.configure_whd_logging(logging.INFO)

    # allow external connections
    any_interface = '0.0.0.0'

    if production:
        uvicorn.run(
            'whdutil:app',
            host=any_interface,
            port=port,
            log_level='info',
            log_config=_logging_config_dict(stdout_level=logging.INFO),
            workers=workers,
            debug=False,
            reload=False,
        )

    else:
        uvicorn.run(
            'whdutil:app',
            host='0.0.0.0',
            port=port,
            log_level='info',
            log_config=_logging_config_dict(stdout_level=logging.DEBUG),
            workers=workers,
            debug=True,
            reload=True,
        )
