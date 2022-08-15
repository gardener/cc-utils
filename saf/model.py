import dataclasses
import enum
import typing

import clamav.cnudie
import gci.componentmodel as cm

dc = dataclasses.dataclass


@dc
class EvidenceMetadata:
    evidence_id: str
    collection_date: str


@dc
class EvidenceRequest:
    meta: EvidenceMetadata
    data: typing.Dict


@dc
class EvidenceRequestV1:
    meta: EvidenceMetadata
    EvidenceDataBinary: typing.Dict


class MalwareScanState(enum.Enum):
    FINISHED_SUCCESSFULLY = 'finished_successfully'
    FINISHED_WITH_ERRORS = 'finished_with_errors'


@dc
class MalwarescanResult:
    resource: cm.Resource
    scan_state: MalwareScanState
    findings: typing.List[str]


@dc
class MalwarescanEvidenceRequestV1(EvidenceRequestV1):
    EvidenceDataBinary: typing.List[clamav.cnudie.ResourceScanResult]
