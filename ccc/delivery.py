import logging

import ci.log
import ci.util
import ctx
import delivery.client

ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def _current_cfg_set():
    cfg_factory = ctx.cfg_factory()
    cfg_set_name = ci.util.current_config_set_name()
    cfg_set = cfg_factory.cfg_set(cfg_set_name)

    return cfg_set


def default_client_if_available(cfg_set=None):
    if not ci.util._running_on_ci() and not cfg_set:
        return None

    if not cfg_set:
        cfg_set = _current_cfg_set()

    try:
        delivery_endpoints = endpoints(cfg_set=cfg_set)
        routes = delivery.client.DeliveryServiceRoutes(
            base_url=delivery_endpoints.base_url(),
        )
        return delivery.client.DeliveryServiceClient(routes=routes)
    except Exception:
        logger.warning('unable to build delivery client')


def endpoints(cfg_set=None):
    if not ci.util._running_on_ci() and not cfg_set:
        return None

    if not cfg_set:
        cfg_set = _current_cfg_set()

    delivery_endpoints = cfg_set.delivery_endpoints()

    return delivery_endpoints
