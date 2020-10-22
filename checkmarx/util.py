import functools
import logging

import ci.util
import checkmarx.client


@functools.lru_cache
def component_logger(component_name):
    return logging.getLogger(component_name)


@functools.lru_cache()
def create_checkmarx_client(checkmarx_cfg_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    return checkmarx.client.CheckmarxClient(cfg_fac.checkmarx(checkmarx_cfg_name))
