import typing

from deliverydb.db import DeliveryDB, delivery_db_no_orm
import model.compliancedb

import psycopg


def default_with_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    hostname: str = None,
    orm: bool = True,
) -> typing.Union[DeliveryDB, psycopg.Connection]:

    if not hostname:
        hostname = db_cfg.hostname()

    if orm:
        return DeliveryDB(
            username=db_cfg.credentials().username(),
            password=db_cfg.credentials().password(),
            hostname=hostname,
            port=db_cfg.port(),
        )

    else:
        return delivery_db_no_orm(
            username=db_cfg.credentials().username(),
            password=db_cfg.credentials().password(),
            hostname=hostname,
            port=db_cfg.port(),
        )
