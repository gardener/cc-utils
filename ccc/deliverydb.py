import psycopg

import deliverydb.db
import model.compliancedb


def sqlalchemy_with_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: str = None,
    overwrite_username: str = None,
    overwrite_password: str = None,
    overwrite_port: int = None,
) -> deliverydb.db.DeliveryDB:

    db_url = deliverydb.db.database_conncetion_url_from_cfg(
        db_cfg=db_cfg,
        overwrite_hostname=overwrite_hostname,
        overwrite_username=overwrite_username,
        overwrite_password=overwrite_password,
        overwrite_port=overwrite_port,
    )

    return deliverydb.db.DeliveryDB(
        db_conn_url=db_url,
    )


def psycopg_with_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: str = None,
    overwrite_username: str = None,
    overwrite_password: str = None,
    overwrite_port: int = None,
) -> psycopg.Connection:

    db_url = deliverydb.db.database_conncetion_url_from_cfg(
        db_cfg=db_cfg,
        overwrite_hostname=overwrite_hostname,
        overwrite_username=overwrite_username,
        overwrite_password=overwrite_password,
        overwrite_port=overwrite_port,
    )

    return psycopg.connect(
        conninfo=db_url,
    )
