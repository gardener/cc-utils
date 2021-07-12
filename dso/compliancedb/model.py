from enum import Enum

import sqlalchemy
from sqlalchemy import Column, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

import dso.model


Base = declarative_base()


class ScanTool(Enum):
    WHITESOURCE = 'whitesource'
    PROTECODE = 'protecode'
    CHECKMARX = 'checkmarx'
    CLAMAV = 'clamav'


class Artifact(Base):
    __tablename__ = 'artifact'

    id = Column(sqlalchemy.String, primary_key=True)
    component_name = Column(sqlalchemy.String)
    component_version = Column(sqlalchemy.String)
    name = Column(sqlalchemy.String)
    type = Column(sqlalchemy.types.Enum(
        dso.model.ArtifactType,
        values_callable=lambda v: [e.value for e in v],
    ))
    version = Column(sqlalchemy.String)

    scan_result = relationship('ScanResult')


class ScanResult(Base):
    __tablename__ = 'scan_result'

    id = Column(sqlalchemy.String, primary_key=True)
    artifact_id = Column(sqlalchemy.String, ForeignKey('artifact.id'))
    timestamp = Column(
        sqlalchemy.TIMESTAMP(timezone=True),
        server_default=func.now(),
    )
    source = Column(sqlalchemy.types.Enum(
        ScanTool,
    ))
    scan_data = Column(sqlalchemy.JSON)
