import dataclasses
import datetime
import typing

import gci.componentmodel as cm

import dso.labels
import unixutil.model


@dataclasses.dataclass
class ScanArtifact:
    name: str
    label: dso.labels.SourceScanLabel
    component: cm.Component
    source: cm.ComponentSource


class Datasource:
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
    artefact_kind: str = 'artefact' # artefact | resource | source


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
    CODECHECKS_AGGREGATED = 'codechecks/aggregated'
    VULNERABILITIES_AGGREGATED = 'vulnerabilities/aggregated'
    VULNERABILITIES_RAW = 'vulnerabilities/raw'
    MALWARE = 'malware'
    LICENSES_AGGREGATED = 'licenses/aggregated'
    COMPONENTS = 'components'
    FILESYSTEM_PATHS = 'filesystem/paths'
    OS_IDS = 'os_ids'
    RESCORING_VULNERABILITIES = 'rescoring/vulnerabilities'


class RelationKind:
    RESCORE = 'rescore'


@dataclasses.dataclass(frozen=True)
class Relation:
    '''
    Describes relation between artefact_metadata.
    This is necessary as "rescorings" (type: "rescoring/vulnerabilities") are stored as
    artefact_metadata, but relate to artefact_metadata (of type "vulnerabilities/aggregated") as they
    rescore vulnerability findings.
    '''
    refers_to: str # see `Datatype` for supported values
    relation_kind: str # see `RelationKind` for supported values


@dataclasses.dataclass(frozen=True)
class Metadata:
    datasource: str
    type: str
    relation: Relation | None = None
    creation_date: datetime.datetime | str = datetime.datetime.now()


@dataclasses.dataclass(frozen=True)
class GreatestCVE:
    greatestCvss3Score: float
    reportUrl: str
    product_id: int
    group_id: int
    base_url: str
    bdba_cfg_name: str


@dataclasses.dataclass(frozen=True)
class OsID:
    os_info: unixutil.model.OperatingSystemId


@dataclasses.dataclass(frozen=True)
class LicenseSummary:
    licenses: list[str]
    reportUrl: str
    productId: int


@dataclasses.dataclass(frozen=True)
class ComponentVersion:
    name: str
    version: str


@dataclasses.dataclass(frozen=True)
class ComponentSummary:
    components: list[ComponentVersion]


@dataclasses.dataclass(frozen=True)
class FilesystemPath:
    path: str
    digest: str


@dataclasses.dataclass(frozen=True)
class FilesystemPaths:
    paths: list[FilesystemPath]


@dataclasses.dataclass(frozen=True)
class ClamAVMetadata:
    clamav_version_str: str
    signature_version: int
    virus_definition_timestamp: datetime.datetime


@dataclasses.dataclass
class MalwareFindingMeta:
    scanned_octets: int
    receive_duration_seconds: float
    scan_duration_seconds: float
    scanned_content_digest: str | None = None


@dataclasses.dataclass
class MalwareFinding:
    status: str
    details: str
    malware_status: str
    meta: MalwareFindingMeta | None
    name: str


@dataclasses.dataclass(frozen=True)
class MalwareSummary:
    '''
    empty list of findings states "no malware found"
    '''
    findings: list[MalwareFinding]
    metadata: ClamAVMetadata


@dataclasses.dataclass(frozen=True)
class CodecheckFindings:
    high: int
    medium: int
    low: int
    info: int


@dataclasses.dataclass(frozen=True)
class CodecheckSummary:
    findings: CodecheckFindings
    risk_rating: int
    risk_severity: int
    overview_url: str
    report_url: str | None


@dataclasses.dataclass(frozen=True)
class VulnerabilityCve:
    cve: str


@dataclasses.dataclass(frozen=True)
class VulnerabilityRescoring:
    vulnerability: VulnerabilityCve
    rescored_severity: str
    matching_rules: list[str]


@dataclasses.dataclass(frozen=True)
class Component:
    name: str
    version: str | None # bdba might be unable to determine a version
    source: str


@dataclasses.dataclass(frozen=True)
class Rescoring:
    component: Component
    rescore_to: list[VulnerabilityRescoring]


@dataclasses.dataclass(frozen=True)
class RescoringData:
    rescorings: list[Rescoring]


@dataclasses.dataclass(frozen=True)
class ArtefactMetadata:
    artefact: ComponentArtefactId
    meta: Metadata
    data: typing.Union[
        ComponentSummary,
        CodecheckSummary,
        FilesystemPaths,
        GreatestCVE,
        LicenseSummary,
        MalwareSummary,
        OsID,
        RescoringData,
        dict, # fallback, there should be a type
    ]
