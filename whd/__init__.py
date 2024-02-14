# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
