import dataclasses
import datetime
import enum
import hashlib
import typing

import dacite
import dateutil.parser

import cnudie.iter
import dso.cvss
import dso.labels
import ocm
import unixutil.model


def _as_key(
    *args,
    separator: str='|',
    absent_indicator: str='None', # be backwards compatible
) -> str:
    return separator.join(absent_indicator if arg is None else arg for arg in args)


class SastStatus(enum.StrEnum):
    NO_LINTER = 'no-linter'


class SastSubType(enum.StrEnum):
    LOCAL_LINTING = 'local-linting'
    CENTRAL_LINTING = 'central-linting'


@dataclasses.dataclass
class MatchCondition:
    component_name: str


class OsStatus(enum.StrEnum):
    NO_BRANCH_INFO = 'noBranchInfo'
    NO_RELEASE_INFO = 'noReleaseInfo'
    UNABLE_TO_COMPARE_VERSION = 'unableToCompareVersion'
    BRANCH_REACHED_EOL = 'branchReachedEol'
    UPDATE_AVAILABLE_FOR_BRANCH = 'updateAvailableForBranch'
    EMPTY_OS_ID = 'emptyOsId'
    AT_MOST_ONE_PATCHLEVEL_BEHIND = 'atMostOnePatchlevelBehind'
    MORE_THAN_ONE_PATCHLEVEL_BEHIND = 'moreThanOnePatchlevelBehind'
    UP_TO_DATE = 'upToDate'
    DISTROLESS = 'distroless'


@dataclasses.dataclass
class ScanArtifact:
    name: str
    label: dso.labels.SourceScanLabel
    component: ocm.Component
    source: ocm.Source


class Datasource:
    ARTEFACT_ENUMERATOR = 'artefact-enumerator'
    BDBA = 'bdba'
    SAST = 'sast'
    CLAMAV = 'clamav'
    CC_UTILS = 'cc-utils'
    OSID = 'osid'
    CRYPTO = 'crypto'
    DELIVERY_DASHBOARD = 'delivery-dashboard'
    DIKI = 'diki'
    FALCO = 'falco'
    INVENTORY = 'inventory'

    @staticmethod
    def datasource_to_datatypes(datasource: str) -> tuple[str, ...]:
        return {
            Datasource.ARTEFACT_ENUMERATOR: (
                Datatype.COMPLIANCE_SNAPSHOTS,
            ),
            Datasource.BDBA: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.VULNERABILITY,
                Datatype.LICENSE,
                Datatype.STRUCTURE_INFO,
                Datatype.RESCORING,
            ),
            Datasource.SAST: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.SAST_FINDING,
                Datatype.RESCORING,
            ),
            Datasource.CLAMAV: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.MALWARE_FINDING,
            ),
            Datasource.CC_UTILS: (
                Datatype.OS_IDS,
            ),
            Datasource.OSID: (
                Datatype.OSID,
                Datatype.OSID_FINDING,
                Datatype.ARTEFACT_SCAN_INFO,
            ),
            Datasource.CRYPTO: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.CRYPTO_ASSET,
                Datatype.CRYPTO,
            ),
            Datasource.DELIVERY_DASHBOARD: (
                Datatype.RESCORING,
            ),
            Datasource.DIKI: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.DIKI_FINDING,
            ),
            Datasource.FALCO: (
                Datatype.FALCO_FINDING,
            ),
            Datasource.INVENTORY: (
                Datatype.INVENTORY_FINDING,
            ),
        }[datasource]

    @staticmethod
    def has_scan_info(datasource: str) -> bool:
        return Datatype.ARTEFACT_SCAN_INFO in Datasource.datasource_to_datatypes(datasource)


def normalise_artefact_extra_id(
    artefact_extra_id: dict[str, str],
) -> str:
    '''
    generate stable representation of `artefact_extra_id`

    sorted by key in alphabetical order and concatinated following pattern:
    key1:value1_key2:value2_ ...
    '''
    s = sorted(artefact_extra_id.items(), key=lambda items: items[0])
    return '_'.join([':'.join(values) for values in s])


@dataclasses.dataclass
class LocalArtefactId:
    artefact_name: str | None = None
    artefact_type: str | None = None
    artefact_version: str | None = None
    artefact_extra_id: dict = dataclasses.field(default_factory=dict)

    @property
    def normalised_artefact_extra_id(self) -> str:
        return normalise_artefact_extra_id(self.artefact_extra_id)

    @property
    def key(self) -> str:
        return _as_key(
            self.artefact_name,
            self.artefact_version,
            self.artefact_type,
            self.normalised_artefact_extra_id,
        )

    def as_dict_repr(self) -> dict:
        return {
            **({'Artefact': self.artefact_name} if self.artefact_name else {}),
            **({'Artefact-Version': self.artefact_version} if self.artefact_version else {}),
            **({'Artefact-Type': self.artefact_type} if self.artefact_type else {}),
            **self.artefact_extra_id,
        }

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other: typing.Self) -> bool:
        if not type(self) == type(other):
            return False
        return self.key == other.key

    def __str__(self) -> str:
        return (
            f'{self.artefact_name}:{self.artefact_version} '
            f'({self.artefact_type=}, {self.artefact_extra_id=})'
        )


class ArtefactKind(enum.StrEnum):
    ARTEFACT = 'artefact'
    RESOURCE = 'resource'
    RUNTIME = 'runtime'
    SOURCE = 'source'


def is_ocm_artefact(artefact_kind: ArtefactKind) -> bool:
    return artefact_kind in (ArtefactKind.RESOURCE, ArtefactKind.SOURCE)


@dataclasses.dataclass
class ComponentArtefactId:
    component_name: str | None = None
    component_version: str | None = None
    artefact: LocalArtefactId | None = None
    artefact_kind: ArtefactKind | None = None
    references: list[typing.Self] = dataclasses.field(default_factory=list)

    @property
    def key(self) -> str:
        artefact_key = self.artefact.key if self.artefact else None
        references_key = _as_key(
            *(
                reference.key
                for reference in sorted(self.references, key=lambda ref: ref.key)
            )
        )

        return _as_key(
            self.component_name,
            self.component_version,
            artefact_key,
            self.artefact_kind,
            references_key,
        )

    def as_dict_repr(self) -> dict:
        return {
            **({'Component': self.component_name} if self.component_name else {}),
            **({'Component-Version': self.component_version} if self.component_version else {}),
            **({'Artefact-Kind': self.artefact_kind} if self.artefact_kind else {}),
            **(self.artefact.as_dict_repr() if self.artefact else {}),
        }

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other: typing.Self) -> bool:
        if not type(self) == type(other):
            return False
        return self.key == other.key

    def __str__(self) -> str:
        return (
            f'{self.component_name}:{self.component_version} '
            f'({self.artefact_kind=}, {self.artefact=})'
        )


def component_artefact_id_from_ocm(
    component: ocm.Component,
    artefact: ocm.Resource | ocm.Source,
) -> ComponentArtefactId:
    local_artefact = LocalArtefactId(
        artefact_name=artefact.name,
        artefact_version=artefact.version,
        artefact_type=artefact.type,
        artefact_extra_id=artefact.extraIdentity,
    )

    if isinstance(artefact, ocm.Resource):
        artefact_kind = ArtefactKind.RESOURCE
    elif isinstance(artefact, ocm.Source):
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
    SAST_FINDING = 'finding/sast'
    DIKI_FINDING = 'finding/diki'
    OS_IDS = 'os_ids'
    OSID_FINDING = 'finding/osid'
    OSID = 'osid'
    RESCORING = 'rescorings'
    COMPLIANCE_SNAPSHOTS = 'compliance/snapshots'
    ARTEFACT_SCAN_INFO = 'meta/artefact_scan_info'
    CRYPTO_ASSET = 'crypto_asset'
    CRYPTO = 'finding/crypto'
    FALCO_FINDING = 'finding/falco'
    INVENTORY_FINDING = 'finding/inventory'

    @staticmethod
    def datatype_to_datasource(datatype: str) -> str:
        return {
            Datatype.LICENSE: Datasource.BDBA,
            Datatype.VULNERABILITY: Datasource.BDBA,
            Datatype.OS_IDS: Datasource.CC_UTILS,
            Datatype.OSID: Datasource.OSID,
            Datatype.MALWARE_FINDING: Datasource.CLAMAV,
            Datatype.DIKI_FINDING: Datasource.DIKI,
            Datatype.SAST_FINDING: Datasource.SAST,
            Datatype.OSID_FINDING: Datasource.OSID,
            Datatype.CRYPTO_ASSET: Datasource.CRYPTO,
            Datatype.CRYPTO: Datasource.CRYPTO,
            Datatype.FALCO_FINDING: Datasource.FALCO,
            Datatype.INVENTORY_FINDING: Datasource.INVENTORY,
        }[datatype]


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
        return _as_key(self.package_name, self.package_version)


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
        return _as_key(self.package_name, self.package_version, self.license.name)


@dataclasses.dataclass(frozen=True)
class VulnerabilityFinding(Finding, BDBAMixin):
    cve: str
    cvss_v3_score: float
    cvss: dso.cvss.CVSSV3 | dict
    summary: str | None

    @property
    def key(self) -> str:
        return _as_key(self.package_name, self.package_version, self.cve)


@dataclasses.dataclass(frozen=True)
class RescoringVulnerabilityFinding:
    package_name: str
    cve: str

    @property
    def key(self) -> str:
        return _as_key(self.package_name, self.cve)


@dataclasses.dataclass(frozen=True)
class RescoringLicenseFinding:
    package_name: str
    license: License

    @property
    def key(self) -> str:
        return _as_key(self.package_name, self.license.name)


@dataclasses.dataclass(frozen=True)
class MalwareFindingDetails:
    filename: str
    content_digest: str
    malware: str
    context: str | None # optional context information, e.g. layer-digest or bucket-id

    @property
    def key(self) -> str:
        return _as_key(self.content_digest, self.filename, self.malware)


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
class SastFinding(Finding):
    sast_status: SastStatus
    sub_type: SastSubType

    @property
    def key(self) -> str:
        return _as_key(self.sast_status, self.sub_type)


@dataclasses.dataclass(frozen=True)
class RescoreSastFinding:
    sast_status: SastStatus
    sub_type: SastSubType

    @property
    def key(self) -> str:
        return _as_key(self.sast_status, self.sub_type)


@dataclasses.dataclass(frozen=True)
class OsIdFinding(Finding):
    osid: unixutil.model.OperatingSystemId
    os_status: OsStatus
    greatest_version: str | None
    eol_date: datetime.datetime | None

    @property
    def key(self) -> str:
        return _as_key(self.osid.ID)

    @property
    def status_description(self) -> str:
        if self.os_status is OsStatus.BRANCH_REACHED_EOL:
            return 'Branch has reached end-of-life'
        elif self.os_status is OsStatus.MORE_THAN_ONE_PATCHLEVEL_BEHIND:
            return 'Image is more than one patchlevel behind'
        elif self.os_status is OsStatus.AT_MOST_ONE_PATCHLEVEL_BEHIND:
            return 'Image is at most one patchlevel behind'
        elif self.os_status in (
            OsStatus.EMPTY_OS_ID,
            OsStatus.NO_BRANCH_INFO,
            OsStatus.NO_RELEASE_INFO,
            OsStatus.UNABLE_TO_COMPARE_VERSION,
        ):
            return f'No valid OS scan result ({self.os_status})'
        else:
            return 'Unknown OSID status'


@dataclasses.dataclass(frozen=True)
class RescoreOsIdFinding:
    osid: unixutil.model.OperatingSystemId

    @property
    def key(self) -> str:
        return _as_key(self.osid.ID)


@dataclasses.dataclass(frozen=True)
class DikiCheck:
    message: str
    targets: list[dict] | dict


@dataclasses.dataclass(frozen=True)
class DikiFinding(Finding):
    provider_id: str
    ruleset_id: str
    ruleset_name: str | None
    ruleset_version: str
    rule_id: str
    rule_name: str | None
    checks: list[DikiCheck]

    @property
    def key(self) -> str:
        return _as_key(self.provider_id, self.ruleset_id, self.rule_id)


class CryptoAssetTypes(enum.StrEnum):
    ALGORITHM = 'algorithm'
    CERTIFICATE = 'certificate'
    LIBRARY = 'library'
    PROTOCOL = 'protocol'
    RELATED_CRYPTO_MATERIAL = 'related-crypto-material'


class Primitives(enum.StrEnum):
    BLOCK_CIPHER = 'block-cipher'
    HASH = 'hash'
    PKE = 'pke'
    SIGNATURE = 'signature'


@dataclasses.dataclass
class AlgorithmProperties:
    name: str
    primitive: Primitives | None
    parameter_set_identifier: str | None
    curve: str | None
    padding: str | None

    @property
    def key(self) -> str:
        return _as_key(
            self.name,
            self.primitive,
            self.parameter_set_identifier,
            self.curve,
            self.padding,
        )


class CertificateKind(enum.StrEnum):
    ROOT_CA = 'root-ca'
    INTERMEDIATE_CA = 'intermediate-ca'
    END_USER = 'end-user'


@dataclasses.dataclass
class CertificateProperties:
    kind: CertificateKind
    validity_years: int | None
    signature_algorithm_ref: str | None
    subject_public_key_ref: str | None

    @property
    def key(self) -> str:
        return _as_key(
            self.kind,
            str(self.validity_years),
            self.signature_algorithm_ref,
            self.subject_public_key_ref,
        )


@dataclasses.dataclass
class LibraryProperties:
    name: str
    version: str | None

    @property
    def key(self) -> str:
        return _as_key(self.name, self.version)


@dataclasses.dataclass
class ProtocolProperties:
    type: str | None
    version: str | None

    @property
    def key(self) -> str:
        return _as_key(self.type, self.version)


@dataclasses.dataclass
class RelatedCryptoMaterialProperties:
    type: str | None
    algorithm_ref: str | None
    curve: str | None
    size: int | None

    @property
    def key(self) -> str:
        return _as_key(self.type, self.algorithm_ref, self.curve, str(self.size))


@dataclasses.dataclass
class CryptoAsset:
    names: list[str]
    locations: list[str]
    asset_type: CryptoAssetTypes
    properties: (
        AlgorithmProperties
        | CertificateProperties
        | LibraryProperties
        | RelatedCryptoMaterialProperties
        | ProtocolProperties
    )

    @property
    def key(self) -> str:
        return _as_key(self.asset_type, self.properties.key)


@dataclasses.dataclass(frozen=True)
class CryptoFinding(Finding):
    standard: str
    asset: CryptoAsset
    summary: str | None

    @property
    def key(self) -> str:
        return _as_key(self.standard, self.asset.key)


@dataclasses.dataclass
class RescoringCryptoFinding:
    standard: str
    asset: CryptoAsset

    @property
    def key(self) -> str:
        return _as_key(self.standard, self.asset.key)


@dataclasses.dataclass(frozen=True)
class InventoryFinding(Finding):
    """
    Represents a finding from the gardener/inventory system
    """
    # Name of provider, where orphan resources originate from, e.g. AWS, Azure,
    # GCP, OpenStack, etc.
    provider_name: str

    # Kind of the orphan resource, e.g. Virtual Machine, Public IP address, etc.
    resource_kind: str

    # Resource name specifies the unique name of the resource in the provider
    resource_name: str

    # Short summary of the finding
    summary: str

    # Additional attributes associated with this finding
    attributes: dict

    @property
    def key(self) -> str:
        return _as_key(self.provider_name, self.resource_kind, self.resource_name)


@dataclasses.dataclass(frozen=True)
class User:
    username: str
    type: str = 'user'

    @property
    def key(self) -> str:
        return _as_key(self.username, self.type)


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
    '''
    The `allowed_processing_time` is stored relatively to allow the rescoring to apply to findings
    with different discovery dates, i.e. in case the rescoring is of scope "global" or "component".
    Alternatively, the explicit `due_date` can be set in case the `due_date` is independent of the
    individual discovery dates (for example, this might be the case if exceptions apply).
    '''
    finding: (
        RescoringVulnerabilityFinding
        | RescoringLicenseFinding
        | MalwareFindingDetails
        | RescoreSastFinding
        | RescoringCryptoFinding
        | RescoreOsIdFinding
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
    allowed_processing_time: str | None = None
    due_date: datetime.date | None = None

    @property
    def key(self) -> str:
        return _as_key(
            self.referenced_type,
            self.severity,
            self.user.key,
            self.comment,
            self.finding.key,
            self.allowed_processing_time,
            self.due_date.strftime('%Y-%m-%d') if self.due_date else None,
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
    due_date: datetime.date
    state: list[ComplianceSnapshotState]

    @property
    def key(self) -> str:
        return self.due_date.isoformat()

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


class FalcoPriority(enum.StrEnum):
    EMERGENCY = 'Emergency'
    ALERT = 'Alert'
    CRITICAL = 'Critical'
    ERROR = 'Error'
    WARNING = 'Warning'
    NOTICE = 'Notice'
    INFORMATIONAL = 'Informational'
    DEBUG = 'Debug'


@dataclasses.dataclass(frozen=True)
class FalcoEvent:
    message: str
    cluster: str
    hostname: str
    time: datetime.datetime
    rule: str
    priority: FalcoPriority
    output: dict[str, typing.Any]


@dataclasses.dataclass(frozen=True)
class Node:
    name: str
    count: int


@dataclasses.dataclass(frozen=True)
class Cluster:
    name: str
    nodes: list[Node]


@dataclasses.dataclass(frozen=True)
class FalcoEventGroup:
    '''
    FalcoEventGroup represents a group of Falco events that are similar in
    nature. In almost all cases those are false positives and can be ignored.
    Falco exceptions can be defined but they can be silenced here.

    :param count int:
        number of events in this group.
    :param group_hash str:
        hash of the group (event fiields and values that form the group),
        can be reconstructed from a sample event and the fields property.
    :param fields dict[str, str]:
        Identical fields that form the group
    :param events list[FalcoEvent]:
        list of events in this group (possibly truncated).
    :param exception ExceptionTemplate:
        exception template for this group
    '''
    message: str
    clusters: list[Cluster]
    landscape: str
    project: str
    rule: str
    priority: FalcoPriority
    first_event: datetime.datetime
    last_event: datetime.datetime
    count: int
    group_hash: str
    fields: dict[str, str]
    events: list[FalcoEvent]
    exception: str

    @property
    def key(self) -> str:
        return self.group_hash


@dataclasses.dataclass(frozen=True)
class FalcoInteractiveEventGroup:
    '''
    Group of events that - most likely - are a result of a single interactive
    session. It might however also be an indication of an attack. These
    events must be reviewed and ideally be linked to some legal activity.

    :param group_hash str:
        reproducible group hash to avoid double reporting should the reporting
        job run multiple times on the same data.
    :param events list[FalcoEvent]:
        List of all events. The goal is not to truncate this list but it might
        have to be done if it gets too large.
    '''
    count: int
    cluster: str
    hostname: str
    project: str
    landscape: str
    group_hash: str
    first_event: datetime.datetime
    last_event: datetime.datetime
    events: list[FalcoEvent]

    @property
    def key(self) -> str:
        return self.group_hash


class FalcoFindingSubType(enum.StrEnum):
    EVENT_GROUP = 'event-group'
    INTERACTIVE_EVENT_GROUP = 'interactive-event-group'


@dataclasses.dataclass(frozen=True)
class FalcoFinding(Finding):
    subtype: FalcoFindingSubType
    finding: FalcoInteractiveEventGroup | FalcoEventGroup

    @property
    def key(self) -> str:
        return self.finding.key


@dataclasses.dataclass
class ArtefactMetadata:
    '''
    Model class to interact with entries of the delivery-db. In the first place, these entries are
    being identified via `ComponentArtefactId` (`artefact` property) as well as their `Datatype`
    (`meta.type` property) and `Datasource` (`meta.datasource` property). If there might be multiple
    entries for this tuple, the `data` object must define an extra `key` property, which allows a
    unique identification together with the tuple of `artefact`, `meta.type` and `meta.datasource`.
    The `id` property (derived from `key`) is intended to be used as private key in the underlying
    database.

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
        | SastFinding
        | DikiFinding
        | OsID
        | OsIdFinding
        | CustomRescoring
        | ComplianceSnapshot
        | CryptoAsset
        | CryptoFinding
        | FalcoFinding
        | InventoryFinding
        | dict # fallback, there should be a type
    )
    discovery_date: datetime.date | None = None # required for finding specific SLA tracking
    allowed_processing_time: str | None = None

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
                    enum.StrEnum,
                    MatchCondition,
                ],
                strict=True,
            ),
        )

    @property
    def key(self) -> str:
        if dataclasses.is_dataclass(self.data):
            data_key = self.data.key if hasattr(self.data, 'key') else None
        else:
            data_key = self.data.get('key')

        return _as_key(self.artefact.key, self.meta.datasource, self.meta.type, data_key)

    @property
    def id(self) -> str:
        return hashlib.blake2s(
            self.key.encode('utf-8'),
            digest_size=16,
            usedforsecurity=False,
        ).hexdigest()


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
