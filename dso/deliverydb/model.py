from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Column, Integer
from sqlalchemy.ext.declarative import declarative_base


Base = declarative_base()


class Scan(Base):
    __tablename__ = 'compliance_issue'

    database_entry_id = Column(Integer, primary_key=True, autoincrement=True)
    artifact = Column(JSONB, nullable=True, default=dict)
    meta = Column(JSONB, nullable=True, default=dict)
    data = Column(JSONB, nullable=True, default=dict)

    def __iter__(self):
        yield 'artifact', self.artifact
        yield 'meta', self.meta
        yield 'data', self.data
