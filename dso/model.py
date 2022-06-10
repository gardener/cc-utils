import dataclasses
import datetime
import typing

import gci.componentmodel as cm

import clamav.client
import dso.labels
import unixutil.model


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
class LocalArtefactId:
    artefact_name: str
    artefact_version: str
    artefact_type: str
    artefact_extra_id: dict


@dataclasses.dataclass(frozen=True)
class ComponentArtefactId:
    component_name: str
    component_version: str
    artefact: LocalArtefactId


def component_artefact_id_from_ocm(
    component: cm.Component,
    artefact: cm.Resource | cm.ComponentSource
) -> ComponentArtefactId:
    local_artefact = LocalArtefactId(
        artefact_name=artefact.name,
        artefact_version=artefact.version,
        artefact_type=artefact.type,
        artefact_extra_id=artefact.extraIdentity,
    )
    return ComponentArtefactId(
        component_name=component.name,
        component_version=component.version,
        artefact=local_artefact,
    )


class Datatype:
    VULNERABILITIES_AGGREGATED = 'vulnerabilities/aggregated'
    VULNERABILITIES_RAW = 'vulnerabilities/raw'
    MALWARE = 'malware'
    LICENSES_AGGREGATED = 'licenses/aggregated'
    COMPONENTS = 'components'
    OS_IDS = 'os_ids'


@dataclasses.dataclass(frozen=True)
class Metadata:
    datasource: str
    type: str
    creation_date: datetime.datetime


@dataclasses.dataclass(frozen=True)
class GreatestCVE:
    greatestCvss3Score: float
    reportUrl: str


@dataclasses.dataclass(frozen=True)
class OsID:
    os_info: unixutil.model.OperatingSystemId


@dataclasses.dataclass(frozen=True)
class LicenseSummary:
    licenses: list[str]


@dataclasses.dataclass(frozen=True)
class ComponentVersion:
    name: str
    version: str


@dataclasses.dataclass(frozen=True)
class Component:
    components: list[ComponentVersion]


@dataclasses.dataclass(frozen=True)
class Malware:
    findings: list[clamav.client.ScanResult]


@dataclasses.dataclass(frozen=True)
class ArtefactMetadata:
    artefact: ComponentArtefactId
    meta: Metadata
    data: GreatestCVE | LicenseSummary | Component | OsID | dict
