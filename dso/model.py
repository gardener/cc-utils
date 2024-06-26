import dataclasses
import datetime
import enum

import dacite
import dateutil.parser

import cnudie.iter
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
    DIKI = 'diki'


def normalise_artefact_extra_id(
    artefact_extra_id: dict[str, str],
    artefact_version: str=None,
) -> str:
    '''
    generate stable representation of `artefact_extra_id` and remove `version` key if
    the specified version is identical to the given artefact version

    sorted by key in alphabetical order and concatinated following pattern:
    key1:value1_key2:value2_ ...
    '''
    if (version := artefact_extra_id.get('version')) and version == artefact_version:
        artefact_extra_id = artefact_extra_id.copy()
        del artefact_extra_id['version']

    s = sorted(artefact_extra_id.items(), key=lambda items: items[0])
    return '_'.join([':'.join(values) for values in s])


@dataclasses.dataclass(frozen=True)
class LocalArtefactId:
    artefact_name: str | None
    artefact_type: str
    artefact_version: str | None = None
    artefact_extra_id: dict = dataclasses.field(default_factory=dict)

    def normalised_artefact_extra_id(
        self,
        remove_duplicate_version: bool=False,
    ) -> str:
        return normalise_artefact_extra_id(
            artefact_extra_id=self.artefact_extra_id,
            artefact_version=self.artefact_version if remove_duplicate_version else None,
        )


class ArtefactKind(enum.StrEnum):
    ARTEFACT = 'artefact'
    RESOURCE = 'resource'
    RUNTIME = 'runtime'
    SOURCE = 'source'


def is_ocm_artefact(artefact_kind: ArtefactKind) -> bool:
    return artefact_kind in (ArtefactKind.RESOURCE, ArtefactKind.SOURCE)


@dataclasses.dataclass(frozen=True)
class ComponentArtefactId:
    component_name: str | None
    component_version: str | None
    artefact: LocalArtefactId
    artefact_kind: ArtefactKind = ArtefactKind.ARTEFACT

    def as_frozenset(self) -> frozenset[str]:
        return frozenset((
            self.component_name,
            self.component_version,
            self.artefact_kind,
            self.artefact.artefact_name,
            self.artefact.artefact_version,
            self.artefact.artefact_type,
            # frozenset(self.artefact.artefact_extra_id.items()),
        ))

    def __hash__(self):
        return hash(self.as_frozenset())

    def __eq__(self, other: 'ComponentArtefactId'):
        return self.as_frozenset() == other.as_frozenset()


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
        artefact_kind = ArtefactKind.RESOURCE
    elif isinstance(artefact, cm.Source):
        artefact_kind = ArtefactKind.SOURCE
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
    MALWARE_FINDING = 'finding/malware'
    DIKI_FINDING = 'finding/diki'
    CODECHECKS_AGGREGATED = 'codechecks/aggregated'
    MALWARE = 'malware'
    OS_IDS = 'os_ids'
    RESCORING = 'rescorings'
    COMPLIANCE_SNAPSHOTS = 'compliance/snapshots'
    ARTEFACT_SCAN_INFO = 'meta/artefact_scan_info'


@dataclasses.dataclass(frozen=True)
class Metadata:
    datasource: str
    type: str
    creation_date: datetime.datetime | str = None
    last_update: datetime.datetime | str | None = None


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
    severity: str | None = None # TODO: rm once finding-specific tracking implemented


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
class BDBAMixin:
    package_name: str
    package_version: str | None # bdba might be unable to determine a version
    base_url: str
    report_url: str
    product_id: int
    group_id: int


@dataclasses.dataclass(frozen=True)
class License:
    name: str


@dataclasses.dataclass(frozen=True)
class FilesystemPathEntry:
    path: str
    type: str


@dataclasses.dataclass(frozen=True)
class FilesystemPath:
    path: list[FilesystemPathEntry]
    digest: str


@dataclasses.dataclass(frozen=True)
class StructureInfo(BDBAMixin):
    licenses: list[License]
    filesystem_paths: list[FilesystemPath]

    @property
    def key(self) -> str:
        return f'{self.package_name}|{self.package_version}'


@dataclasses.dataclass(frozen=True)
class Finding:
    '''
    Base class for artefact metadata which is interpreted as a finding. "Finding" as in it has a
    severity and might become object of being rescored.
    '''
    severity: str


@dataclasses.dataclass(frozen=True)
class LicenseFinding(Finding, BDBAMixin):
    license: License

    @property
    def key(self) -> str:
        return f'{self.package_name}|{self.package_version}|{self.license.name}'


@dataclasses.dataclass(frozen=True)
class VulnerabilityFinding(Finding, BDBAMixin):
    cve: str
    cvss_v3_score: float
    cvss: dso.cvss.CVSSV3 | dict
    summary: str | None

    @property
    def key(self) -> str:
        return f'{self.package_name}|{self.package_version}|{self.cve}'


@dataclasses.dataclass(frozen=True)
class RescoringVulnerabilityFinding:
    package_name: str
    cve: str

    @property
    def key(self) -> str:
        return f'{self.package_name}|{self.cve}'


@dataclasses.dataclass(frozen=True)
class RescoringLicenseFinding:
    package_name: str
    license: License

    @property
    def key(self) -> str:
        return f'{self.package_name}|{self.license.name}'


@dataclasses.dataclass(frozen=True)
class MalwareFindingDetails:
    filename: str
    content_digest: str
    malware: str
    context: str | None # optional context information, e.g. layer-digest or bucket-id

    @property
    def key(self) -> str:
        return f'{self.content_digest}|{self.filename}|{self.malware}'


@dataclasses.dataclass(frozen=True)
class ClamAVMalwareFinding(Finding):
    finding: MalwareFindingDetails
    octets_count: int
    scan_duration_seconds: float
    clamav_version: str | None
    signature_version: int | None
    freshclam_timestamp: datetime.datetime | None

    @property
    def key(self) -> str:
        return self.finding.key


@dataclasses.dataclass(frozen=True)
class DikiCheck:
    message: str
    targets: list[dict]


@dataclasses.dataclass(frozen=True)
class DikiFinding(Finding):
    provider_id: str
    ruleset_id: str
    ruleset_version: str
    rule_id: str
    checks: list[DikiCheck]

    @property
    def key(self) -> str:
        return f'{self.provider_id}|{self.ruleset_id}:{self.ruleset_version}|{self.rule_id}'


@dataclasses.dataclass(frozen=True)
class User:
    username: str
    type: str = 'user'

    @property
    def key(self) -> str:
        return f'{self.username}|{self.type}'


@dataclasses.dataclass(frozen=True, kw_only=True)
class BDBAUser(User):
    email: str
    firstname: str
    lastname: str
    type: str = 'bdba-user'


@dataclasses.dataclass(frozen=True, kw_only=True)
class GitHubUser(User):
    github_hostname: str
    type: str = 'github-user'


class MetaRescoringRules(enum.StrEnum):
    BDBA_TRIAGE = 'bdba-triage'
    CUSTOM_RESCORING = 'custom-rescoring'
    ORIGINAL_SEVERITY = 'original-severity'


@dataclasses.dataclass(frozen=True)
class CustomRescoring:
    finding: (
        RescoringVulnerabilityFinding
        | RescoringLicenseFinding
        | MalwareFindingDetails
    )
    referenced_type: str
    severity: str
    user: (
        BDBAUser
        | GitHubUser
        | User
    )
    matching_rules: list[str] = dataclasses.field(default_factory=list)
    comment: str | None = None

    @property
    def key(self) -> str:
        return (
            f'{self.referenced_type}|{self.severity}|{self.user.key}|'
            f'{self.comment}|{self.finding.key}'
        )


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

    @property
    def key(self) -> str:
        return f'{self.cfg_name}|{self.correlation_id}'

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
    '''
    Model class to interact with entries of the delivery-db. In the first place, these entries are
    being identified via `ComponentArtefactId` (`artefact` property) as well as their `Datatype`
    (`meta.type` property). If there might be multiple entries for this tuple, the `data` object
    must define an extra `key` property, which allows a unique identification together with the
    tuple of `artefact` and `meta.type`.

    If an instance of a datatype should become object of being rescored, the `data` property must
    derive from the `Finding` class and implement the `severity` property. Also, a corresponding
    rescoring finding type must be implemented. Apart from the `key` and `severity` property, the
    `data` object may have an arbitrary structure.
    '''
    artefact: ComponentArtefactId
    meta: Metadata
    data: (
        StructureInfo
        | LicenseFinding
        | VulnerabilityFinding
        | ClamAVMalwareFinding
        | DikiFinding
        | CodecheckSummary
        | MalwareSummary
        | OsID
        | CustomRescoring
        | ComplianceSnapshot
        | dict # fallback, there should be a type
    )
    id: int | None = None
    discovery_date: datetime.date | None = None # required for finding specific SLA tracking

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=ArtefactMetadata,
            data=raw,
            config=dacite.Config(
                type_hooks={
                    datetime.datetime: dateutil.parser.isoparse,
                    datetime.date: lambda date: datetime.datetime.fromisoformat(date).date(),
                },
                cast=[
                    ArtefactKind,
                    ComplianceSnapshotStatuses,
                    MetaRescoringRules,
                ],
            ),
        )


def artefact_scan_info(
    artefact_node: cnudie.iter.ArtefactNode,
    datasource: str,
    data: dict={},
) -> ArtefactMetadata:
    '''
    The `data` property may contain extra information about the scan, e.g. a reference to the scan.

    Predefined `data` property for BDBA scan infos:

    data:
        report_url <str>
    '''
    now = datetime.datetime.now()

    artefact_ref = component_artefact_id_from_ocm(
        component=artefact_node.component,
        artefact=artefact_node.artefact,
    )

    meta = Metadata(
        datasource=datasource,
        type=Datatype.ARTEFACT_SCAN_INFO,
        creation_date=now,
        last_update=now,
    )

    return ArtefactMetadata(
        artefact=artefact_ref,
        meta=meta,
        data=data,
    )
