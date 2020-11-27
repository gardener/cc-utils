import functools
import logging


@functools.lru_cache
def component_logger(name: str):
    return logging.getLogger(name)
