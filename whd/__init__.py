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
import os

import ctx


def configure_whd_logging(stdout_level=None):
    if not stdout_level:
        stdout_level = logging.INFO

    cfg = ctx._default_logging_config(stdout_level)
    cfg['handlers'].update({
        'rotating_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'default',
            'level': stdout_level,
            'filename': os.path.join('/', 'tmp', 'whd_log'),
            'backupCount': 1,
            'maxBytes': 50*1024*1024,  # 50 MiB
        },
    })
    cfg.update({
        'loggers': {
            'whd': {
                'level': logging.DEBUG,
                'handlers': ['rotating_file',],
            },
        },
    })
    logging.config.dictConfig(cfg)
