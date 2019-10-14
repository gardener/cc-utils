import functools

import util

ctx = util.ctx()


@functools.lru_cache()
def cfg_factory():
    return ctx.cfg_factory()
