import psycopg

import deliverydb.db
import model.compliancedb


def sqlalchemy_with_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: str = None,
) -> deliverydb.db.DeliveryDB:

    if overwrite_hostname:
        db_url = deliverydb.db.database_connection_url_from_custom(
            hostname=overwrite_hostname,
            username=db_cfg.credentials().username(),
            password=db_cfg.credentials().password(),
            port=db_cfg.port(),
        )
    else:
        db_url = deliverydb.db.database_conncetion_url_from_cfg(
            db_cfg=db_cfg,
        )

    return deliverydb.db.DeliveryDB(
        db_conn_url=db_url,
    )


def psycopg_with_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: str = None,
) -> deliverydb.db.DeliveryDB:

    if overwrite_hostname:
        db_url = deliverydb.db.database_connection_url_from_custom(
            hostname=overwrite_hostname,
            username=db_cfg.credentials().username(),
            password=db_cfg.credentials().password(),
            port=db_cfg.port(),
        )

    else:
        db_url = deliverydb.db.database_conncetion_url_from_cfg(
            db_cfg=db_cfg,
        )

    return psycopg.connect(
        conninfo=db_url,
    )
