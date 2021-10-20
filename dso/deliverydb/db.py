import logging

import sqlalchemy

import ci.util
from dso.deliverydb.model import Base
import dso.model
import dso.util


class DeliveryDB:
    def __init__(
        self,
        username: str,
        password: str,
        hostname: str,
        port: int,
        dialect: str = 'postgresql',
    ):
        self._engine = sqlalchemy.create_engine(
            f'{dialect}://{username}:{password}@{hostname}:{port}',
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
        scan = dso.deliverydb.model.Scan(
            artifact=artifact,
            meta=meta,
            data=data,
        )

        self.Session.add(scan)


def make_deliverydb(
    deliverydb_cfg_name: str,
) -> DeliveryDB:
    cfg_fac = ci.util.ctx().cfg_factory()
    cfg = cfg_fac.compliancedb(deliverydb_cfg_name)
    return DeliveryDB(
        username=cfg.credentials().username(),
        password=cfg.credentials().password(),
        hostname=cfg.hostname(),
        port=cfg.port(),
    )
