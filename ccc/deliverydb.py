import ci.util
from dso.deliverydb.db import DeliveryDB


def default_with_cfg_name(
    cfg_name: str,
    hostname: str = None,
) -> DeliveryDB:
    cfg_fac = ci.util.ctx().cfg_factory()
    cfg = cfg_fac.compliancedb(cfg_name)
    return DeliveryDB(
        username=cfg.credentials().username(),
        password=cfg.credentials().password(),
        hostname=cfg.hostname() if not hostname else hostname,
        port=cfg.port(),
    )
