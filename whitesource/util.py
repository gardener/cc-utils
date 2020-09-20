# SPDX-FileCopyrightText: 2020 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import functools

import ci.util
import whitesource.client


@functools.lru_cache()
def create_whitesource_client(whitesource_cfg_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    return whitesource.client.WhitesourceClient(cfg_fac.whitesource(whitesource_cfg_name))
