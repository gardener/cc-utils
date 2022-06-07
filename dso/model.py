import dataclasses
import datetime
import typing

import gci.componentmodel as cm

import dso.labels


# abstraction of component model v2 source and resource
@dataclasses.dataclass
class ScanArtifact:
    name: str
    access: typing.Union[
        cm.OciAccess,
        cm.GithubAccess,
        cm.HttpAccess,
        cm.ResourceAccess,
    ]
    label: dso.labels.ScanningHint


class Datasource:
    WHITESOURCE = 'whitesource'
    BDBA = 'bdba' # formerly protecode
    CHECKMARX = 'checkmarx'
    CLAMAV = 'clamav'
    CC_UTILS = 'cc-utils'


@dataclasses.dataclass(frozen=True)
class Artefact:
    artefact_name: str
    artefact_version: str
    artefact_type: str
    artefact_extra_id: dict


@dataclasses.dataclass(frozen=True)
class ArtefactReference:
    component_name: str
    component_version: str
    artefact: Artefact


def artefact_ref_from_ocm(
    component: cm.Component,
    artefact: cm.Resource | cm.ComponentSource
) -> ArtefactReference:
    artefact = Artefact(
        artefact_name=artefact.name,
        artefact_version=artefact.version,
        artefact_type=artefact.type,
        artefact_extra_id=artefact.extraIdentity,
    )
    return ArtefactReference(
        component_name=component.name,
        component_version=component.version,
        artefact=artefact,
    )


class Datatype:
    VULNERABILITIES_AGGREGATED = 'vulnerabilities/aggregated'
    VULNERABILITIES_RAW = 'vulnerabilities/raw'
    MALWARE_RAW = 'malware/raw'
    LICENSES_AGGREGATED = 'licenses/aggregated'
    COMPONENTS_BDBA = 'components/bdba'
    OS_IDS_RAW = 'os_ids/raw'


@dataclasses.dataclass(frozen=True)
class Metadata:
    datasource: str
    type: str
    creation_date: datetime.datetime


@dataclasses.dataclass(frozen=True)
class GreatestCVE:
    greatestCvss3Score: typing.Optional[float]
    reportUrl: str


@dataclasses.dataclass(frozen=True)
class FindingMeta:
    scanned_octets: int
    receive_duration_seconds: float
    scan_duration_seconds: float


@dataclasses.dataclass(frozen=True)
class Finding:
    result: str
    message: str
    details: typing.Optional[str]
    meta: FindingMeta


@dataclasses.dataclass(frozen=True)
class ClamavFinding:
    findings: list[Finding]


@dataclasses.dataclass(frozen=True)
class OsInfo:
    name: str
    id: str
    pretty_name: str
    cpe_name: str
    variant: str
    variant_id: str
    version: str
    version_id: str
    version_codename: str
    build_id: str
    image_id: str
    image_version: str


@dataclasses.dataclass(frozen=True)
class OsID:
    osInfo: OsInfo


@dataclasses.dataclass(frozen=True)
class License:
    licenses: list[str]


@dataclasses.dataclass(frozen=True)
class ComponentVersion:
    name: str
    version: str


@dataclasses.dataclass(frozen=True)
class Component:
    components: list[ComponentVersion]


@dataclasses.dataclass(frozen=True)
class ArtefactMetadata:
    artefact: ArtefactReference
    meta: Metadata
    data: GreatestCVE | License | Component | OsID | dict
