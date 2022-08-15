import dataclasses
import typing


dc = dataclasses.dataclass


@dc
class EvidenceMetadata:
    evidence_id: str
    collection_date: str


@dc
class EvidenceRequestV1:
    meta: EvidenceMetadata
    EvidenceDataBinary: typing.Dict
