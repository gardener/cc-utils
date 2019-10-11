import functools

import ci.util

ctx = ci.util.ctx()


@functools.lru_cache()
def cfg_factory():
    return ctx.cfg_factory()
