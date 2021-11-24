import logging

import sqlalchemy

from deliverydb.model import Base, Scan
import model.compliancedb


class DeliveryDB:
    def __init__(
        self,
        db_conn_url: str,
    ):
        self._engine = sqlalchemy.create_engine(
            db_conn_url,
            echo=True,
            future=True,
        )

        self.Base = Base
        # we configured our own root logger and use log propagation
        # therefore pop streamhandler to not have duplicate output
        logging.getLogger('sqlalchemy.engine.Engine').handlers.pop()
        self.Base.metadata.create_all(self._engine)
        self.Session = sqlalchemy.orm.Session(self._engine)

    def insert_compliance_issue(
        self,
        artifact: dict,
        meta: dict,
        data: dict,
    ):
        scan = Scan(
            artifact=artifact,
            meta=meta,
            data=data,
        )

        self.Session.add(scan)


def database_conncetion_url_from_cfg(
    db_cfg: model.compliancedb.ComplianceDbConfig,
    dialect: str = 'postgresql',
    overwrite_hostname: str = None,
    overwrite_username: str = None,
    overwrite_password: str = None,
    overwrite_port: int = None,
) -> str:
    username = overwrite_username if overwrite_username else db_cfg.credentials().username()
    password = overwrite_password if overwrite_password else db_cfg.credentials().password()
    hostname = overwrite_hostname if overwrite_hostname else db_cfg.hostname()
    port = overwrite_port if overwrite_port else db_cfg.port()

    # Potentially, this function could be used to create connection urls
    # to different types of databases, therefore the "dialect" can vary.
    return f'{dialect}://{username}:{password}@{hostname}:{port}'
