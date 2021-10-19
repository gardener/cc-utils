from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Column, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.mutable import MutableDict


Base = declarative_base()


class Scan(Base):
    __tablename__ = 'compliance_issue'

    database_entry_id = Column(Integer, primary_key=True, autoincrement=True)
    # combination of hstore and mutabledict to allow in-place changes to the dict.
    # https://docs.sqlalchemy.org/en/14/dialects/
    # postgresql.html#sqlalchemy.dialects.postgresql.HSTORE
    artifact = Column(MutableDict.as_mutable(JSONB), nullable=True, default=dict)
    meta = Column(MutableDict.as_mutable(JSONB), nullable=True, default=dict)
    data = Column(MutableDict.as_mutable(JSONB), nullable=True, default=dict)
