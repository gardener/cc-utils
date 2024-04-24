import dataclasses
import datetime
import enum

import dso.cvss
import dso.labels
import gci.componentmodel as cm
import unixutil.model


@dataclasses.dataclass
class ScanArtifact:
    name: str
    label: dso.labels.SourceScanLabel
    component: cm.Component
    source: cm.ComponentSource


class Datasource:
    ARTEFACT_ENUMERATOR = 'artefact-enumerator'
    BDBA = 'bdba'
    CHECKMARX = 'checkmarx'
    CLAMAV = 'clamav'
    CC_UTILS = 'cc-utils'


@dataclasses.dataclass(frozen=True)
class LocalArtefactId:
    artefact_name: str | None
    artefact_version: str | None
    artefact_type: str
    artefact_extra_id: dict


@dataclasses.dataclass(frozen=True)
class ComponentArtefactId:
    component_name: str | None
    component_version: str | None
    artefact: LocalArtefactId
    artefact_kind: str = 'artefact' # artefact | resource | source

    def as_frozenset(self) -> frozenset[str]:
        return frozenset((
            self.component_name,
            self.component_version,
            self.artefact_kind,
            self.artefact.artefact_name,
            self.artefact.artefact_version,
            self.artefact.artefact_type,
            frozenset(self.artefact.artefact_extra_id.items()),
        ))


def component_artefact_id_from_ocm(
    component: cm.Component,
    artefact: cm.Resource | cm.Source,
) -> ComponentArtefactId:
    local_artefact = LocalArtefactId(
        artefact_name=artefact.name,
        artefact_version=artefact.version,
        artefact_type=artefact.type,
        artefact_extra_id=artefact.extraIdentity,
    )

    if isinstance(artefact, cm.Resource):
        artefact_kind = 'resource'
    elif isinstance(artefact, cm.Source):
        artefact_kind = 'source'
    else:
        # should not occur
        raise TypeError(artefact)

    return ComponentArtefactId(
        component_name=component.name,
        component_version=component.version,
        artefact=local_artefact,
        artefact_kind=artefact_kind,
    )


class Datatype:
    STRUCTURE_INFO = 'structure_info'
    LICENSE = 'finding/license'
    VULNERABILITY = 'finding/vulnerability'
    CODECHECKS_AGGREGATED = 'codechecks/aggregated'
    MALWARE = 'malware'
    OS_IDS = 'os_ids'
    RESCORING = 'rescorings'
    COMPLIANCE_SNAPSHOTS = 'compliance/snapshots'


class RelationKind:
    RESCORE = 'rescore'


@dataclasses.dataclass(frozen=True)
class Relation:
    '''
    Describes relation between artefact_metadata.
    This is necessary as "rescorings" are stored as artefact_metadata, but relate to
    artefact_metadata (of type "finding/vulnerability") as they rescore vulnerability findings.
    '''
    refers_to: str # see `Datatype` for supported values
    relation_kind: str # see `RelationKind` for supported values


@dataclasses.dataclass(frozen=True)
class Metadata:
    datasource: str
    type: str
    relation: Relation | None = None
    creation_date: datetime.datetime | str = None
    last_update: datetime.datetime | str = None


@dataclasses.dataclass(frozen=True)
class OsID:
    os_info: unixutil.model.OperatingSystemId


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
class BDBAPackageId:
    package_name: str
    package_version: str | None # bdba might be unable to determine a package version
    source: str = Datasource.BDBA


@dataclasses.dataclass(frozen=True)
class BDBAScanId:
    base_url: str
    report_url: str
    product_id: int
    group_id: int
    source: str = Datasource.BDBA


@dataclasses.dataclass(frozen=True)
class License:
    name: str


@dataclasses.dataclass(frozen=True)
class FilesystemPath:
    path: str
    digest: str


@dataclasses.dataclass(frozen=True)
class StructureInfo:
    id: BDBAPackageId
    scan_id: BDBAScanId
    licenses: list[License]
    filesystem_paths: list[FilesystemPath]


@dataclasses.dataclass(frozen=True)
class Finding:
    id: BDBAPackageId
    scan_id: BDBAScanId
    severity: str


@dataclasses.dataclass(frozen=True)
class LicenseFinding(Finding):
    license: License


@dataclasses.dataclass(frozen=True)
class VulnerabilityFinding(Finding):
    cve: str
    cvss_v3_score: float
    cvss: dso.cvss.CVSSV3 | dict
    summary: str | None


@dataclasses.dataclass(frozen=True)
class RescoringFinding:
    id: BDBAPackageId


@dataclasses.dataclass(frozen=True)
class RescoringVulnerabilityFinding(RescoringFinding):
    cve: str


@dataclasses.dataclass(frozen=True)
class RescoringLicenseFinding(RescoringFinding):
    license: License


@dataclasses.dataclass(frozen=True)
class CustomRescoring:
    finding: (
        RescoringVulnerabilityFinding
        | RescoringLicenseFinding
    )
    severity: str
    user: dict
    matching_rules: list[str] = dataclasses.field(default_factory=list)
    comment: str | None = None


class ComplianceSnapshotStatuses(enum.StrEnum):
    ACTIVE = 'active'
    INACTIVE = 'inactive'


@dataclasses.dataclass(frozen=True)
class ComplianceSnapshotState:
    timestamp: datetime.datetime
    status: ComplianceSnapshotStatuses | str | int | None = None
    service: str | None = None


@dataclasses.dataclass(frozen=True)
class ComplianceSnapshot:
    cfg_name: str
    latest_processing_date: datetime.date
    correlation_id: str
    state: list[ComplianceSnapshotState]

    def current_state(
        self,
        service: str = None,
    ) -> ComplianceSnapshotState | None:
        for state in sorted(self.state, key=lambda s: s.timestamp, reverse=True):
            if service == state.service:
                return state
        return None

    def purge_old_states(
        self,
        service: str = None,
    ):
        current_state = None
        for state in sorted(self.state, key=lambda s: s.timestamp, reverse=True):
            if not service == state.service:
                continue

            if not current_state:
                current_state = state
                continue

            self.state.remove(state)


@dataclasses.dataclass(frozen=True)
class ArtefactMetadata:
    artefact: ComponentArtefactId
    meta: Metadata
    data: (
        StructureInfo
        | LicenseFinding
        | VulnerabilityFinding
        | CodecheckSummary
        | MalwareSummary
        | OsID
        | CustomRescoring
        | ComplianceSnapshot
        | dict # fallback, there should be a type
    )
    id: int | None = None
    discovery_date: datetime.date | None = None # required for finding specific SLA tracking
