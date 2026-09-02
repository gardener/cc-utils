import collections.abc
import dataclasses
import datetime
import enum
import functools
import graphlib
import io
import json
import logging
import typing
import os
import urllib.parse

try:
    import dacite
    _have_dacite = True
except ImportError:
    _have_dacite = False

# optional dependencies

try:
    import jsonschema
    _have_jsonschema = True
except ImportError:
    _have_jsonschema = False
    # validate-method will fail

try:
    import yaml
    _have_yaml = True
except ImportError:
    _have_yaml = False
    # we will output in JSON-format


dc = dataclasses.dataclass
own_dir = os.path.dirname(__file__)
default_json_schema_path = os.path.join(
    own_dir,
    'ocm-component-descriptor-schema.yaml',
)

logger = logging.getLogger(__name__)


class ValidationMode(enum.StrEnum):
    FAIL = 'fail'
    WARN = 'warn'


class SchemaVersion(enum.StrEnum):
    V1 = 'v1'
    V2 = 'v2'


class AccessType(enum.StrEnum):
    GITHUB = 'github' # XXX: new: gitHub/v1
    HELM = 'Helm/v1'
    LOCAL_BLOB = 'localBlob/v1'
    NONE = 'None'  # the resource is only declared informally (e.g. generic)
    NPM = 'NPM/v1'
    OCI_BLOB = 'ociBlob/v1'
    OCI_REGISTRY = 'ociRegistry' # XXX: new: ociArtifact/v1
    RELATIVE_OCI_REFERENCE = 'relativeOciReference'
    S3 = 's3' # XXX: new: s3/v1
    S3_V2 = 's3/v2'


# hack: patch enum to accept "aliases"
# -> the values defined in enum above will be  used for serialisation; the aliases are also
# accepted for deserialisation
# note: the `/v1` suffix is _always_ optional (if absent, /v1 is implied)
AccessType._value2member_map_ |= {
    'GitHub': AccessType.GITHUB,
    'gitHub': AccessType.GITHUB, # deprecated
    'github': AccessType.GITHUB, # deprecated
    'github/v1': AccessType.GITHUB, # deprecated
    'Helm': AccessType.HELM,
    'helm': AccessType.HELM, # deprecated
    'LocalBlob': AccessType.LOCAL_BLOB,
    'localBlob': AccessType.LOCAL_BLOB, # deprecated
    'localFilesystemBlob': AccessType.LOCAL_BLOB,
    'none': AccessType.NONE,
    'NPM': AccessType.NPM,
    'npm': AccessType.NPM, # deprecated
    'OCIImage': AccessType.OCI_REGISTRY,
    'ociArtifact': AccessType.OCI_REGISTRY, # deprecated
    'ociArtifact/v1': AccessType.OCI_REGISTRY, # deprecated
    'ociArtefact': AccessType.OCI_REGISTRY, # deprecated
    'OCIRegistry': AccessType.OCI_REGISTRY, # deprecated
    'OCIRegistry/v1': AccessType.OCI_REGISTRY, # deprecated
    'ociRegistry': AccessType.OCI_REGISTRY, # deprecated
    'ociImage': AccessType.OCI_REGISTRY, # deprecated
    'OCIImageLayer': AccessType.OCI_BLOB,
    'ociBlob': AccessType.OCI_BLOB, # deprecated
    'S3': AccessType.S3,
    's3': AccessType.S3, # deprecated
    's3/v1': AccessType.S3, # deprecated
    'S3/v2': AccessType.S3_V2,
    's3/v2': AccessType.S3_V2, # deprecated
}

AccessTypeOrStr = AccessType | str


@dc(kw_only=True)
class Access:
    type: AccessTypeOrStr | None = AccessType.NONE


class AccessDict(dict):
    '''
    fallback for unknown access-types; it is api-compatible to `Access` in that it exposes its type
    via the `type` attribute mimicking behaviour of `dataclasses` from this module, but otherwise
    behaves as a `dict` (thus allowing de/reserialisation using dacite/dataclasses.asdict w/o losing
    attributes).
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not 'type' in self:
            raise ValueError('attribute `type` must be present')

        self.type = self.get('type')


@dc(kw_only=True)
class LocalBlobGlobalAccess:
    digest: str
    mediaType: str
    ref: str
    size: int
    type: str


@dc(kw_only=True)
class LocalBlobAccess(Access):
    '''
    a blob that is accessible locally to the component-descriptor

    see: https://github.com/open-component-model/ocm-spec/blob/d74b6a210ff8c8c3486aa9b21e22c169d014806e/doc/04-extensions/01-extensions.md#localblob # noqa
    '''
    type: AccessTypeOrStr = AccessType.LOCAL_BLOB
    localReference: str
    size: int | None = None
    mediaType: str = 'application/data'
    referenceName: str | None = None
    globalAccess: LocalBlobGlobalAccess | dict | None = None


@dc(kw_only=True)
class OciAccess(Access):
    type: AccessType = AccessType.OCI_REGISTRY
    imageReference: str


@dc(kw_only=True)
class OciBlobAccess(OciAccess):
    type: AccessTypeOrStr = AccessType.OCI_BLOB
    mediaType: str
    digest: str
    size: int


@dc(kw_only=True)
class RelativeOciAccess(Access):
    reference: str
    type: AccessType = AccessType.RELATIVE_OCI_REFERENCE


@dc(kw_only=True)
class GithubAccess(Access):
    repoUrl: str
    ref: str | None = None
    commit: str | None = None
    type: AccessTypeOrStr = AccessType.GITHUB

    def __post_init__(self):
        parsed = self._normalise_and_parse_url()
        if not len(parsed.path[1:].split('/')):
            raise ValueError(f'{self.repoUrl=} must have exactly two path components')

    def _normalise_and_parse_url(self):
        parsed = urllib.parse.urlparse(self.repoUrl)
        if not parsed.scheme:
            # prepend dummy-schema to properly parse hostname and path (and rm it again later)
            parsed = urllib.parse.urlparse('dummy://' + self.repoUrl)

        return parsed

    def repository_name(self):
        return self._normalise_and_parse_url().path[1:].split('/')[1]

    def org_name(self):
        return self._normalise_and_parse_url().path[1:].split('/')[0]

    def hostname(self):
        return self._normalise_and_parse_url().hostname


@dc(kw_only=True)
class S3Access(Access):
    type: AccessTypeOrStr | None = AccessType.S3
    bucket: str
    key: str
    mediaType: str | None = None
    region: str | None = None


@dc(kw_only=True)
class LegacyS3Access(Access):
    type: AccessTypeOrStr | None = AccessType.S3_V2
    bucketName: str
    objectKey: str
    mediaType: str | None = None
    region: str | None = None


@dc(kw_only=True)
class HelmAccess(Access):
    type: AccessTypeOrStr = AccessType.HELM
    helmRepository: str
    helmChart: str
    caCert: str | None = None
    keyring: str | None = None


@dc(kw_only=True)
class NPMAccess(Access):
    type: AccessTypeOrStr = AccessType.NPM
    registry: str
    package: str
    version: str


class ArtefactType(enum.StrEnum):
    BLOB = 'blob/v1'
    COSIGN_SIGNATURE = 'cosignSignature'
    DIRECTORY_TREE = 'directoryTree'
    EXECUTABLE = 'executable'
    GIT = 'git'
    HELM_CHART = 'helmChart/v1'
    NPM_PACKAGE = 'npmPackage'
    OCI_ARTEFACT = 'ociArtifact/v1'
    OCI_IMAGE = 'ociImage'
    SBOM = 'sbom'


# hack: patch enum to accept "aliases"
# -> the values defined in enum above will be  used for serialisation; the aliases are also
# accepted for deserialisation
# note: the `/v1` suffix is _always_ optional (if absent, /v1 is implied)
ArtefactType._value2member_map_ |= {
    'blob': ArtefactType.BLOB,
    'executable': ArtefactType.EXECUTABLE,
    'directoryTree': ArtefactType.DIRECTORY_TREE,
    'filesystem': ArtefactType.DIRECTORY_TREE,
    'git': ArtefactType.GIT,
    'git/v1': ArtefactType.GIT,
    'helmChart': ArtefactType.HELM_CHART,
    'npmPackage': ArtefactType.NPM_PACKAGE,
    'ociArtifact': ArtefactType.OCI_ARTEFACT,
    'ociImage': ArtefactType.OCI_IMAGE,
    'ociImage/v1': ArtefactType.OCI_IMAGE,
    'sbom': ArtefactType.SBOM,
}


class ResourceRelation(enum.StrEnum):
    LOCAL = 'local'
    EXTERNAL = 'external'


@dc
class MergeSpec:
    algorithm: str | None = None # OCM schema requires /^[a-z][a-z0-9/_-]+$/
    config: str | int | float | bool | dict | list | None = None


@dc(frozen=True)
class Label:
    name: str
    value: str | int | float | bool | dict | list
    version: str | None = None # OCM schema requires /^v[0-9]+$/
    signing: bool = False
    # merge: MergeSpec | None = None # `null` aka. `None` is not allowed by JSON-schema
    # TODO when re-enabling: remove from normalisation again


_no_default = object()


class LabelMethodsMixin:
    def find_label(
        self,
        name: str,
        default=_no_default,
        raise_if_absent: bool = False,
    ):
        for label in self.labels:
            if label.name == name:
                return label
        else:
            if default is _no_default and raise_if_absent:
                raise ValueError(f'no such label: {name=}')
            if default is _no_default:
                return None
            return default

    def set_label(
        self,
        label: Label,
        raise_if_present: bool = False,
    ) -> list[Label]:
        if self.find_label(name=label.name) and raise_if_present:
            raise ValueError(f'label {label.name} is already present')

        patched_labels = [l for l in self.labels if l.name != label.name]
        patched_labels.append(label)

        return dataclasses.replace(
            self,
            labels=patched_labels,
        )


class NormalisationAlgorithm(enum.StrEnum):
    JSON_NORMALISATION = 'jsonNormalisation/v1'
    OCI_ARTIFACT_DIGEST = 'ociArtifactDigest/v1'
    GENERIC_BLOB_DIGEST = 'genericBlobDigest/v1'


@dc
class DigestSpec:
    hashAlgorithm: str
    normalisationAlgorithm: NormalisationAlgorithm | str
    value: str

    @property
    def oci_tag(self) -> str:
        return f'sha256:{self.value}'


# EXCLUDE_FROM_SIGNATURE used in digest field for normalisationAlgorithm
# (in combination with NO_DIGEST for hashAlgorithm and value) to indicate
# the resource content should not be part of the signature
EXCLUDE_FROM_SIGNATURE = "EXCLUDE-FROM-SIGNATURE"

# NO_DIGEST used in digest field for hashAlgorithm and value
# (in combination with EXCLUDE_FROM_SIGNATURE for normalisationAlgorithm)
# to indicate the resource content should not be part of the signature
NO_DIGEST = "NO-DIGEST"


@dc
class ExcludeFromSignatureDigest(DigestSpec):
    '''
    ExcludeFromSignatureDigest is a special digest notation to indicate the resource
    content should not be part of the signature
    '''
    hashAlgorithm: str = NO_DIGEST
    normalisationAlgorithm: str = EXCLUDE_FROM_SIGNATURE
    value: str = NO_DIGEST


@dc
class SignatureSpec:
    algorithm: str
    value: str
    mediaType: str


@dc
class TimestampSpec:
    value: str | None = None
    time: str | None = None # date-time according to RFC3339 (rounded to seconds): %Y-%m-%dT%H:%M:%SZ


@dc
class Signature:
    name: str
    digest: DigestSpec
    signature: SignatureSpec
    # timestamp: TimestampSpec | None = None # `null` aka. `None` is not allowed by JSON-schema


@dc
class Metadata:
    schemaVersion: SchemaVersion = SchemaVersion.V2


class ArtifactIdentity:
    def __init__(self, name, **kwargs):
        self.name = name
        kwargs['name'] = name
        # ensure stable order to ensure stable sort order
        self._id_attrs = tuple(sorted(kwargs.items(), key=lambda i: i[0]))

    def __str__(self):
        return '-'.join((a[1] for a in self._id_attrs))

    def __len__(self):
        return len(self._id_attrs)

    def __eq__(self, other):
        if not type(self) == type(other):
            return False
        return self._id_attrs == other._id_attrs

    def __hash__(self):
        return hash((type(self), self._id_attrs))

    def __lt__(self, other):
        if not type(self) == type(other):
            return False
        return self._id_attrs.__lt__(other._id_attrs)

    def __le__(self, other):
        if not type(self) == type(other):
            return False
        return self._id_attrs.__le__(other._id_attrs)

    def __ne__(self, other):
        if not type(self) == type(other):
            return False
        return self._id_attrs.__ne__(other._id_attrs)

    def __gt__(self, other):
        if not type(self) == type(other):
            return False
        return self._id_attrs.__gt__(other._id_attrs)

    def __ge__(self, other):
        if not type(self) == type(other):
            return False
        return self._id_attrs.__ge__(other._id_attrs)


class ComponentReferenceIdentity(ArtifactIdentity):
    pass


class ResourceIdentity(ArtifactIdentity):
    pass


class SourceIdentity(ArtifactIdentity):
    pass


@dc(frozen=True)
class ComponentIdentity:
    name: str
    version: str


class Artifact(LabelMethodsMixin):
    '''
    base class for ComponentReference, Resource, Source
    '''
    def identity(self, peers: collections.abc.Sequence['Artifact']):
        '''
        returns the identity-object for this artifact (component-ref, resource, or source).

        Note that, the `version` attribute is implicitly added iff
        there would otherwise be a conflict among peers (i.e. two or more artifacts share
        the same name and extraIdentity).

        In future versions of component-descriptor, this behaviour will be discontinued. It will
        instead be regarded as an error if the IDs of a given sequence of artifacts (declared by
        one component-descriptor) are not all pairwise different.
        '''
        own_type = type(self)
        for p in peers:
            if not type(p) == own_type:
                raise ValueError(f'all peers must be of same type {type(self)=} {type(p)=}')

        if own_type is ComponentReference:
            IdCtor = ComponentReferenceIdentity
        elif own_type is Resource:
            IdCtor = ResourceIdentity
        elif own_type is Source:
            IdCtor = SourceIdentity
        else:
            raise NotImplementedError(own_type)

        # pylint: disable=E1101
        identity = IdCtor(
            name=self.name,
            **(self.extraIdentity or {})
        )

        if not peers:
            return identity

        # check whether there are collissions
        for peer in peers:
            if peer is self:
                continue
            if peer.identity(peers=()) == identity:
                # there is at least one collision — add version as tiebreaker while
                # preserving extraIdentity so resources with the same name+version but
                # different extraIdentity (e.g. different SBOM formats) remain distinct.
                # Strip 'name'/'version' from extraIdentity to avoid duplicate-keyword errors
                # if a resource happens to carry those keys there (unusual but valid).
                # pylint: disable=E1101
                extra = {
                    k: v for k, v in (self.extraIdentity or {}).items()
                    if k not in ('name', 'version')
                }
                return IdCtor(
                    name=self.name,
                    version=self.version,
                    **extra,
                )
        # there were no collisions
        return identity


@dc
class ComponentReference(Artifact, LabelMethodsMixin):
    name: str
    componentName: str
    version: str
    digest: DigestSpec | None = None
    extraIdentity: dict[str, str] = dataclasses.field(default_factory=dict)
    labels: list[Label] = dataclasses.field(default_factory=tuple)

    @property
    def component_id(self) -> ComponentIdentity:
        return ComponentIdentity(
            name=self.componentName,
            version=self.version,
        )


@dc
class SourceReference(LabelMethodsMixin):
    identitySelector: dict[str, str]
    labels: list[Label] = dataclasses.field(default_factory=tuple)


@dc
class Resource(Artifact, LabelMethodsMixin):
    name: str
    version: str
    type: ArtefactType | str
    access: (
        # Order of types is important for deserialization. The first matching type will be taken,
        # i.e. keep generic accesses at the bottom of the list
        GithubAccess
        | LocalBlobAccess
        | OciBlobAccess
        | OciAccess
        | RelativeOciAccess
        | S3Access
        | LegacyS3Access
        | HelmAccess
        | NPMAccess
        | dict
        | None
    )
    digest: DigestSpec | None = None
    extraIdentity: dict[str, str] = dataclasses.field(default_factory=dict)
    relation: ResourceRelation = ResourceRelation.LOCAL
    labels: list[Label] = dataclasses.field(default_factory=tuple)
    srcRefs: list[SourceReference] = dataclasses.field(default_factory=tuple)

    def __post_init__(self):
        if dataclasses.is_dataclass(access := self.access):
            return

        if isinstance(access, dict):
            if not 'type' in access:
                raise ValueError('attribute `type` must be present')
            self.access = AccessDict(access)


@dc(kw_only=True, frozen=True)
class OcmRepository:
    type: AccessTypeOrStr


@dc(kw_only=True, frozen=True)
class OciOcmRepository(OcmRepository):
    baseUrl: str
    subPath: str | None = None
    type: AccessTypeOrStr = AccessType.OCI_REGISTRY

    @property
    def oci_ref(self):
        if not self.subPath:
            return self.baseUrl
        return f'{self.baseUrl.rstrip("/")}/{self.subPath.lstrip("/")}'

    def component_oci_ref(self, name, /):
        if isinstance(name, (Component, ComponentIdentity)):
            name = name.name

        return '/'.join((
            self.oci_ref,
            'component-descriptors',
            name.lstrip('/').lower(), # oci-spec only allows lowercase
        ))

    def component_version_oci_ref(
        self,
        name,
        version: str=None,
    ):
        if isinstance(name, (Component, ComponentIdentity)):
            if not version:
                version = name.version
            name = name.name

        if not version:
            name, version = name.rsplit(':', 1)

        return f'{self.component_oci_ref(name)}:{version}'


@dc
class Source(Artifact, LabelMethodsMixin):
    name: str
    access: GithubAccess | dict
    version: str | None = None  # introduce this backwards-compatible for now
    extraIdentity: dict[str, str] = dataclasses.field(default_factory=dict)
    type: ArtefactType | str = ArtefactType.GIT
    labels: list[Label] = dataclasses.field(default_factory=list)

    def __post_init__(self):
        if dataclasses.is_dataclass(access := self.access):
            return

        if isinstance(access, dict):
            if not 'type' in access:
                raise ValueError('attribute `type` must be present')
            self.access = AccessDict(access)


@dc
class Component(LabelMethodsMixin):
    name: str     # must be valid URL w/o schema
    version: str  # relaxed semver

    repositoryContexts: list[OciOcmRepository]
    provider: str | dict

    sources: list[Source]
    componentReferences: list[ComponentReference]
    resources: list[Resource]

    labels: list[Label] = dataclasses.field(default_factory=list)

    creationTime: str | None = None

    @property
    def component(self) -> typing.Self:
        '''
        returns a reference to self. This is a convenience-shortcut for making it easier to
        ensure `ocm.Component` is present in cases where either `ocm.ComponentDescriptor` or
        `ocm.Component` are accepted.
        '''
        return self

    @property
    def current_ocm_repo(self):
        if not self.repositoryContexts:
            return None
        return self.repositoryContexts[-1]

    def identity(self):
        return ComponentIdentity(name=self.name, version=self.version)

    def iter_artefacts(self) -> collections.abc.Generator[Source | Resource, None, None]:
        if self.sources:
            yield from self.sources
        if self.resources:
            yield from self.resources

    def set_current_ocm_repo(self, ocm_repo: OciOcmRepository):
        if not self.current_ocm_repo:
            self.repositoryContexts = [ocm_repo]
        elif self.current_ocm_repo.oci_ref != ocm_repo.oci_ref:
            self.repositoryContexts.append(ocm_repo)
        else:
            pass # current OCM repo is already the desired one


@dc
class NestedDigestSpec:
    name: str
    version: str | None
    extraIdentity: dict[str, str] = dataclasses.field(default_factory=dict)
    digest: DigestSpec | None = None


@dc
class NestedComponentDigests:
    name: str     # must be valid URL w/o schema
    version: str  # relaxed semver
    digest: DigestSpec | None = None
    resourceDigests: list[NestedDigestSpec] = dataclasses.field(default_factory=list)


@functools.lru_cache
def _read_schema_file(schema_file_path: str):
    with open(schema_file_path) as f:
        if not _have_yaml:
            raise RuntimeError('yaml package not available')
        return yaml.safe_load(f)


def enum_or_string(
    v,
    enum_type: enum.Enum,
    omit_v1_version: bool=False,
):
    if omit_v1_version and str(v).endswith('/v1'):
        stripped_value = str(v).removesuffix('/v1')
    else:
        stripped_value = None

    try:
        return enum_type(stripped_value if stripped_value is not None else v)
    except ValueError:
        return str(v)


@dc
class ComponentDescriptor:
    meta: Metadata
    component: Component
    signatures: list[Signature] = dataclasses.field(default_factory=list)
    nestedDigests: list[NestedComponentDigests] = dataclasses.field(default_factory=list)

    @staticmethod
    def validate(
        component_descriptor_dict: dict,
        validation_mode: ValidationMode=ValidationMode.FAIL,
        json_schema_file_path: str = None,
    ):
        if not _have_jsonschema:
            raise RuntimeError('jsonschema package not available - validation cannot be done')

        validation_mode = ValidationMode(validation_mode)
        json_schema_file_path = json_schema_file_path or default_json_schema_path
        schema_dict = _read_schema_file(json_schema_file_path)

        try:
            jsonschema.validate(
                instance=component_descriptor_dict,
                schema=schema_dict,
            )
        except jsonschema.ValidationError as e:
            if validation_mode is ValidationMode.WARN:
                logger.warn(f'Error when validating Component Descriptor: {e}')
            elif validation_mode is ValidationMode.FAIL:
                raise
            else:
                raise ValueError(validation_mode)

    @staticmethod
    def from_dict(
        component_descriptor_dict: dict,
        validation_mode: ValidationMode | None=None,
    ):
        def dateparse(v):
            if not v:
                return None
            if isinstance(v, datetime.datetime):
                return v
            return datetime.datetime.fromisoformat(v)

        if not _have_dacite:
            raise RuntimeError('not available without dacite')

        # Normalize null arrays to empty lists for dacite
        def normalize_nulls(obj):
            # Null values for these keys will be replaced by empty lists, null values for other keys
            # will be kept as-is
            null_allowed_for = (
                'repositoryContexts',
                'sources',
                'componentReferences',
                'resources',
            )

            if isinstance(obj, dict):
                return {
                    k: normalize_nulls(v) if v is not None else ([] if k in null_allowed_for else v)
                    for k, v in obj.items()
                }
            elif isinstance(obj, list):
                return [normalize_nulls(item) for item in obj]
            return obj

        component_descriptor_dict = normalize_nulls(component_descriptor_dict)

        component_descriptor = dacite.from_dict(
            data_class=ComponentDescriptor,
            data=component_descriptor_dict,
            config=dacite.Config(
                cast=[
                    SchemaVersion,
                    ResourceRelation,
                ],
                type_hooks={
                    AccessType | str: functools.partial(
                        enum_or_string, enum_type=AccessType, omit_v1_version=True
                    ),
                    ArtefactType | str: functools.partial(
                        enum_or_string, enum_type=ArtefactType, omit_v1_version=True
                    ),
                    ArtifactIdentity | str: functools.partial(
                        enum_or_string, enum_type=ArtefactType, omit_v1_version=True
                    ),
                    AccessType: functools.partial(
                        enum_or_string, enum_type=AccessType, omit_v1_version=True
                    ),
                    datetime.datetime: dateparse,
                },
            )
        )
        if validation_mode is not None:
            ComponentDescriptor.validate(
                component_descriptor_dict=component_descriptor_dict,
                validation_mode=validation_mode,
            )

        return component_descriptor

    def to_fobj(self, fileobj: io.BytesIO):
        raw_dict = dataclasses.asdict(self)
        if _have_yaml:
            yaml.dump(
                data=raw_dict,
                stream=fileobj,
                Dumper=EnumValueYamlDumper,
            )
        else:
            json.dump(
                obj=raw_dict,
                fp=fileobj,
                cls=EnumJSONEncoder,
            )


if _have_yaml:
    class EnumValueYamlDumper(yaml.SafeDumper):
        '''
        a yaml.SafeDumper that will dump enum objects using their values
        '''
        def represent_data(self, data):
            if isinstance(data, AccessDict):
                # yaml dumper won't know how to parse objects of type `AccessDict`
                # (altough it is just a wrapped dict) -> so convert it to a "real" dict
                data = dict(data)
            if dataclasses.is_dataclass(data):
                data = dataclasses.asdict(data)
            if isinstance(data, enum.Enum):
                return self.represent_data(data.value)
            return super().represent_data(data)


class EnumJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, AccessDict):
            # yaml dumper won't know how to parse objects of type `AccessDict`
            # (altough it is just a wrapped dict) -> so convert it to a "real" dict
            o = dict(o)
        if dataclasses.is_dataclass(o):
            o = dataclasses.asdict(o)
        if isinstance(o, enum.Enum):
            return o.value
        elif isinstance(o, datetime.datetime):
            return o.isoformat()
        return super().default(o)


# shortcut-aliases
Callable = collections.abc.Callable
Iterable = collections.abc.Iterable


# Type-Aliases (for interoperability w/ external implementations)
ComponentName = (
    Component
    | ComponentDescriptor
    | ComponentIdentity
    | ComponentReference
    | str
    | tuple[str, str]
)
ComponentId = ComponentName  # alias; both coerce between name and identity
OcmRepositoryLookup = Callable[
    [ComponentName],
    Iterable[OciOcmRepository],
]
ComponentDescriptorLookup = Callable[
    [ComponentIdentity, OcmRepositoryLookup],
    ComponentDescriptor
]
VersionLookup = Callable[
    [ComponentName, OcmRepository],
    Iterable[str],
]


def to_component_id(
    component: ComponentId, /
) -> ComponentIdentity:
    if isinstance(component, ComponentIdentity):
        return component

    if isinstance(component, ComponentDescriptor) or hasattr(component, 'component'):
        component = component.component
        # fall through to next case
    if isinstance(component, Component) or hasattr(component, 'name') \
        and not hasattr(component, 'componentName'):
        name = component.name
        version = component.version
    if isinstance(component, ComponentReference) or hasattr(component, 'componentName'):
        name = component.componentName
        version = component.version
    if isinstance(component, str):
        try:
            name, version = component.split(':', 1)
        except ValueError as ve:
            ve.add_note(f'{component=}')
            raise
    if isinstance(component, tuple):
        name, version = component

    return ComponentIdentity(
        name=name,
        version=version,
    )


def to_component_name(
    component: ComponentName, /
) -> str:
    if isinstance(component, ComponentDescriptor):
        component = component.component
    if isinstance(component, Component):
        component = component.name
    elif isinstance(component, ComponentIdentity):
        component = component.name
    elif isinstance(component, ComponentReference):
        component = component.componentName
    elif isinstance(component, tuple):
        if not len(component) == 2:
            raise ValueError('expected two-tuple with two elements')
        component = component[0]
    if not isinstance(component, str):
        raise ValueError(component)

    if ':' in component:
        # assumption: has form <name>:<version>
        # let exception raise in other cases
        component, _ = component.split(':')

    return component


def to_component(
    component: Component | ComponentDescriptor, /
) -> Component:
    if isinstance(component, Component):
        return component
    if isinstance(component, ComponentDescriptor):
        return component.component
    raise ValueError(component)


def iter_sorted(
    components: collections.abc.Iterable[Component | ComponentDescriptor], /
) -> collections.abc.Generator[Component, None, None]:
    '''
    returns a generator yielding the given components, honouring their dependencies, starting
    with "leaf" components (i.e. components w/o dependencies), also known as topologically sorted.
    '''
    components = (to_component(c) for c in components)
    components_by_id = {c.identity(): c for c in components}

    toposorter = graphlib.TopologicalSorter()

    def ref_to_comp_id(component_ref: ComponentReference) -> ComponentIdentity:
        return ComponentIdentity(
            name=component_ref.componentName,
            version=component_ref.version,
        )

    for component_id, component in components_by_id.items():
        depended_on_comp_ids = (
            ref_to_comp_id(cref)
            for cref in component.componentReferences
        )
        toposorter.add(component_id, *depended_on_comp_ids)

    for component_id in toposorter.static_order():
        if component_id not in components_by_id:
            # ignore component-references not contained in passed components for now
            continue

        yield components_by_id[component_id]
