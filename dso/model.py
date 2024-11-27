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


@dataclasses.dataclass
class ScanArtifact:
    name: str
    label: dso.labels.SourceScanLabel
    component: ocm.Component
    source: ocm.Source


class Datasource:
    ARTEFACT_ENUMERATOR = 'artefact-enumerator'
    BDBA = 'bdba'
    SAST_LINT_CHECK = 'sast-lint-check'
    CHECKMARX = 'checkmarx'
    CLAMAV = 'clamav'
    CC_UTILS = 'cc-utils'
    CRYPTO = 'crypto'
    DELIVERY_DASHBOARD = 'delivery-dashboard'
    DIKI = 'diki'

    @staticmethod
    def datasource_to_datatypes(datasource: str) -> tuple[str]:
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
            Datasource.SAST_LINT_CHECK: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.SAST_FINDING,
                Datatype.RESCORING,
            ),
            Datasource.CHECKMARX: (
                Datatype.CODECHECKS_AGGREGATED,
            ),
            Datasource.CLAMAV: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.MALWARE_FINDING,
            ),
            Datasource.CC_UTILS: (
                Datatype.OS_IDS,
            ),
            Datasource.CRYPTO: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.CRYPTO_ASSET,
                Datatype.FIPS_FINDING,
            ),
            Datasource.DELIVERY_DASHBOARD: (
                Datatype.RESCORING,
            ),
            Datasource.DIKI: (
                Datatype.ARTEFACT_SCAN_INFO,
                Datatype.DIKI_FINDING,
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
    CODECHECKS_AGGREGATED = 'codechecks/aggregated'
    OS_IDS = 'os_ids'
    RESCORING = 'rescorings'
    COMPLIANCE_SNAPSHOTS = 'compliance/snapshots'
    ARTEFACT_SCAN_INFO = 'meta/artefact_scan_info'
    CRYPTO_ASSET = 'crypto_asset'
    FIPS_FINDING = 'finding/fips'

    @staticmethod
    def datatype_to_datasource(datatype: str) -> str:
        return {
            Datatype.LICENSE: Datasource.BDBA,
            Datatype.VULNERABILITY: Datasource.BDBA,
            Datatype.OS_IDS: Datasource.CC_UTILS,
            Datatype.CODECHECKS_AGGREGATED: Datasource.CHECKMARX,
            Datatype.MALWARE_FINDING: Datasource.CLAMAV,
            Datatype.DIKI_FINDING: Datasource.DIKI,
            Datatype.CRYPTO_ASSET: Datasource.CRYPTO,
            Datatype.FIPS_FINDING: Datasource.CRYPTO,
            Datatype.SAST_FINDING: Datasource.SAST_LINT_CHECK,
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
class DikiCheck:
    message: str
    targets: list[dict] | dict


@dataclasses.dataclass(frozen=True)
class DikiFinding(Finding):
    provider_id: str
    ruleset_id: str
    ruleset_version: str
    rule_id: str
    checks: list[DikiCheck]

    @property
    def key(self) -> str:
        return _as_key(self.provider_id, f'{self.ruleset_id}:{self.ruleset_version}', self.rule_id)


class AssetTypes(enum.StrEnum):
    ALGORITHM = 'algorithm'
    CERTIFICATE = 'certificate'
    LIBRARY = 'library'
    PROTOCOL = 'protocol'
    RELATED_CRYPTO_MATERIAL = 'related-crypto-material'


@dataclasses.dataclass
class AlgorithmProperties:
    name: str
    primitive: str | None = None
    parameter_set_identifier: str | None = None
    curve: str | None = None
    padding: str | None = None

    @property
    def key(self) -> str:
        return _as_key(
            self.name,
            self.primitive,
            self.parameter_set_identifier,
            self.curve,
            self.padding,
        )


@dataclasses.dataclass
class CertificateProperties:
    signature_algorithm_ref: str | None = None
    subject_public_key_ref: str | None = None

    @property
    def key(self) -> str:
        return _as_key(self.signature_algorithm_ref, self.subject_public_key_ref)


@dataclasses.dataclass
class LibraryProperties:
    name: str
    version: str | None = None

    @property
    def key(self) -> str:
        return _as_key(self.name, self.version)


@dataclasses.dataclass
class ProtocolProperties:
    type: str | None = None
    version: str | None = None

    @property
    def key(self) -> str:
        return _as_key(self.type, self.version)


@dataclasses.dataclass
class RelatedCryptoMaterialProperties:
    type: str | None = None
    algorithm_ref: str | None = None
    size: int | None = None

    @property
    def key(self) -> str:
        return _as_key(self.type, self.algorithm_ref, str(self.size))


@dataclasses.dataclass
class CryptoAsset:
    names: list[str]
    locations: list[str]
    asset_type: AssetTypes
    properties: (
        AlgorithmProperties
        | CertificateProperties
        | LibraryProperties
        | RelatedCryptoMaterialProperties
        | ProtocolProperties
    )
    count: int = 1

    @property
    def key(self) -> str:
        return _as_key(self.asset_type, self.properties.key)


@dataclasses.dataclass(frozen=True)
class FipsFinding(Finding):
    asset: CryptoAsset
    summary: str | None = None

    @property
    def key(self) -> str:
        return self.asset.key


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
    finding: (
        RescoringVulnerabilityFinding
        | RescoringLicenseFinding
        | MalwareFindingDetails
        | CryptoAsset
        | SastFinding
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
        return _as_key(
            self.referenced_type,
            self.severity,
            self.user.key,
            self.comment,
            self.finding.key,
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
        return _as_key(self.cfg_name, self.correlation_id)

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
        | CodecheckSummary
        | OsID
        | CustomRescoring
        | ComplianceSnapshot
        | CryptoAsset
        | FipsFinding
        | dict # fallback, there should be a type
    )
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
                    AssetTypes,
                    SastSubType,
                    SastStatus,
                    MatchCondition
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
