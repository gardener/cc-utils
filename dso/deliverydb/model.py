from sqlalchemy.dialects.postgresql import HSTORE
from sqlalchemy import Column, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.mutable import MutableDict


Base = declarative_base()


class Scan(Base):
    __tablename__ = 'compliance_issue'

    id = Column(Integer, primary_key=True)
    # combination of hstore and mutabledict to allow in-place changes to the dict.
    # https://docs.sqlalchemy.org/en/14/dialects/
    # postgresql.html#sqlalchemy.dialects.postgresql.HSTORE
    data = Column(MutableDict.as_mutable(HSTORE))
