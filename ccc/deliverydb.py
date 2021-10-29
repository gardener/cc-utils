from dso.deliverydb.db import DeliveryDB
import model.compliancedb


def default_with_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: bool,
    hostname: str,
) -> DeliveryDB:

    if not overwrite_hostname:
        hostname = db_cfg.hostname()

    return DeliveryDB(
        username=db_cfg.credentials().username(),
        password=db_cfg.credentials().password(),
        hostname=hostname,
        port=db_cfg.port(),
    )
