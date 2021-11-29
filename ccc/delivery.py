import logging

import ci.log
import ci.util
import delivery.client

ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def default_client_if_available():
    if not ci.util._running_on_ci():
        return None

    import ctx
    cfg_factory = ctx.cfg_factory()
    cfg_set_name = ci.util.current_config_set_name()
    cfg_set = cfg_factory.cfg_set(cfg_set_name)

    try:
        delivery_endpoints = cfg_set.delivery_endpoints()
        routes = delivery.client.DeliveryServiceRoutes(
            base_url=delivery_endpoints.service_host(),
        )
        return delivery.client.DeliveryServiceClient(routes=routes)
    except Exception:
        logger.warning('unable to build delivery client')
