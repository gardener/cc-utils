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

import ci.log


def configure_whd_logging(print_thread_id: bool=True):
    whd = logging.getLogger('whd')
    whd.setLevel(logging.DEBUG)
    whd_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join('/', 'tmp', 'whd_log'),
        backupCount=1,
        maxBytes=50 * 1024 * 1024,  # 50 MiB
    )
    whd_handler.setFormatter(
        ci.log.CCFormatter(
            fmt=ci.log.default_fmt_string(print_thread_id=print_thread_id)
        ),
    )
    whd.addHandler(whd_handler)
