import dataclasses
import logging
import typing

import sqlalchemy

from dso.deliverydb.model import Base
import dso.model
import dso.util
import protecode.model


class DeliveryDB:
    def __init__(
        self,
        username: str,
        password: str,
        hostname: str,
        port: int,
        dialect: str = 'postgresql',
    ):
        self.logger = logging.getLogger(__name__)
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

    def _insert(
        self,
        issue: dso.model.ComplianceIssue,
    ):
        self.logger.info('inserting result to deliverydb')
        scan = dso.deliverydb.model.Scan(
            id=dataclasses.asdict(issue.id),
            meta=dataclasses.asdict(issue.meta),
            data=issue.data,
        )

        self.Session.add(scan)
        self.Session.commit()

    def insert_compliance_issue(
        self,
        datasource: dso.model.Datasource,
        # extend typehints with more integrations
        scan: typing.Union[protecode.model.UploadResult],
    ):
        if datasource is dso.model.Datasource.PROTECODE:
            issue = dso.util.upload_result_to_compliance_issue(
                upload_result=scan,
            )
            self._insert(
                issue=issue,
            )
        else:
            raise NotImplementedError(datasource)


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
