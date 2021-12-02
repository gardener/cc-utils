import psycopg
import psycopg_pool

import deliverydb.db
import model.compliancedb


def sqlalchemy_from_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: str = None,
    overwrite_username: str = None,
    overwrite_password: str = None,
    overwrite_port: int = None,
) -> deliverydb.db.DeliveryDB:

    db_url = database_connection_url_from_cfg(
        db_cfg=db_cfg,
        overwrite_hostname=overwrite_hostname,
        overwrite_username=overwrite_username,
        overwrite_password=overwrite_password,
        overwrite_port=overwrite_port,
    )

    return deliverydb.db.DeliveryDB(
        db_conn_url=db_url,
    )


def psycopg_connection_from_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: str = None,
    overwrite_username: str = None,
    overwrite_password: str = None,
    overwrite_port: int = None,
) -> psycopg.Connection:

    db_url = database_connection_url_from_cfg(
        db_cfg=db_cfg,
        overwrite_hostname=overwrite_hostname,
        overwrite_username=overwrite_username,
        overwrite_password=overwrite_password,
        overwrite_port=overwrite_port,
    )

    return psycopg.connect(
        conninfo=db_url,
    )


def psycopg_connection_pool_from_db_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    overwrite_hostname: str = None,
    overwrite_username: str = None,
    overwrite_password: str = None,
    overwrite_port: int = None,
    min_size: int = 1,
    max_size: int = 4,
) -> psycopg_pool.ConnectionPool:
    """
    Please define pool size with cautious.
    Rule of thumb for pool size is:
    pool_size = ((core_count * 2) + effective_spindle_count)

    Detailed analysis about pool sizing:
    https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing
    """

    db_url = database_connection_url_from_cfg(
        db_cfg=db_cfg,
        overwrite_hostname=overwrite_hostname,
        overwrite_username=overwrite_username,
        overwrite_password=overwrite_password,
        overwrite_port=overwrite_port,
    )

    return psycopg_pool.ConnectionPool(
        conninfo=db_url,
        min_size=min_size,
        max_size=max_size,
    )


def database_connection_url_from_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    dialect: str = 'postgresql',
    overwrite_hostname: str = None,
    overwrite_username: str = None,
    overwrite_password: str = None,
    overwrite_port: int = None,
) -> str:
    # use overwrite values if present
    username = overwrite_username or db_cfg.credentials().username()
    password = overwrite_password or db_cfg.credentials().password()
    hostname = overwrite_hostname or db_cfg.hostname()
    port = overwrite_port or db_cfg.port()

    # Potentially, this function could be used to create connection urls
    # to different types of databases, therefore the "dialect" can vary.
    return f'{dialect}://{username}:{password}@{hostname}:{port}'
