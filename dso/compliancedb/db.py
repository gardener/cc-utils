import logging

import sqlalchemy

from dso.compliancedb.model import Base


class ComplianceDB:
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
