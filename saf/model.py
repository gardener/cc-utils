import dataclasses
import enum
import typing


# generic evidence-model
class TargetType(enum.Enum):
    OCM_COMPONENT = 'ocm/component'
    OCM_RESOURCE = 'ocm/resource'


@dataclasses.dataclass
class MetadataTarget:
    id: int
    type: TargetType


@dataclasses.dataclass(kw_only=True)
class ResourceTarget(MetadataTarget):
    name: str
    version: str
    extra_id: dict | None
    type: TargetType = TargetType.OCM_RESOURCE


@dataclasses.dataclass(kw_only=True)
class ComponentTarget(MetadataTarget):
    name: str
    version: str
    type: TargetType = TargetType.OCM_COMPONENT


@dataclasses.dataclass
class EvidenceMetadata:
    pipeline_url: str | None
    targets: typing.List[MetadataTarget]
    evidence_id: str
    collection_date: str


@dataclasses.dataclass
class EvidenceRequest:
    meta: EvidenceMetadata
    EvidenceDataBinary: typing.Dict
