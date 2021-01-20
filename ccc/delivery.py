import ci.util
import delivery.client


def default_client_if_available():
    if not ci.util._running_on_ci():
        return None

    import ctx
    cfg_factory = ctx.cfg_factory()
    cfg_set_name = ci.util.current_config_set_name()
    cfg_set = cfg_factory.cfg_set(cfg_set_name)

    try:
        delivery_cfg = cfg_set.delivery()
        routes = delivery.client.DeliveryServiceRoutes(
            base_url=delivery_cfg.service().external_host(),
        )
        return delivery.client.DeliveryServiceClient(routes=routes)
    except:
        return None
