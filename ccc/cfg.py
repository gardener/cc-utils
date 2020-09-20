# SPDX-FileCopyrightText: 2020 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import functools

import ci.util

ctx = ci.util.ctx()


@functools.lru_cache()
def cfg_factory():
    return ctx.cfg_factory()
