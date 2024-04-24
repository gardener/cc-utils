import dataclasses
import datetime
import enum
import functools
import io
import logging
import typing
import urllib.parse

import dacite
import dateutil.parser
import deprecated
import jsonschema
import yaml

from . import default_json_schema_path

dc = dataclasses.dataclass

logger = logging.getLogger(__name__)


class ValidationMode(enum.StrEnum):
    FAIL = 'fail'
    WARN = 'warn'
    NONE = 'none'


class SchemaVersion(enum.StrEnum):
    V1 = 'v1'
    V2 = 'v2'


class AccessType(enum.StrEnum):
    GITHUB = 'github' # XXX: new: gitHub/v1
    LOCAL_BLOB = 'localBlob/v1'
    NONE = 'None'  # the resource is only declared informally (e.g. generic)
    OCI_BLOB = 'ociBlob/v1'
    OCI_REGISTRY = 'ociRegistry' # XXX: new: ociArtifact/v1
    RELATIVE_OCI_REFERENCE = 'relativeOciReference'
    S3 = 's3' # XXX: new: s3/v1


# hack: patch enum to accept "aliases"
# -> the values defined in enum above will be  used for serialisation; the aliases are also
# accepted for deserialisation
# note: the `/v1` suffix is _always_ optional (if absent, /v1 is implied)
AccessType._value2member_map_ |= {
    'github/v1': AccessType.GITHUB,
    'localBlob': AccessType.LOCAL_BLOB,
    'localFilesystemBlob': AccessType.LOCAL_BLOB,
    'none': AccessType.NONE,
    'OCIRegistry': AccessType.OCI_REGISTRY,
    'OCIRegistry/v1': AccessType.OCI_REGISTRY,
    'ociArtefact': AccessType.OCI_REGISTRY,
    'ociArtifact': AccessType.OCI_REGISTRY,
    'ociArtifact/v1': AccessType.OCI_REGISTRY,
    's3/v1': AccessType.S3,
}

AccessTypeOrStr = typing.Union[AccessType, str]


@dc(frozen=True, kw_only=True)
class Access:
    type: typing.Optional[AccessTypeOrStr] = AccessType.NONE


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


@dc(frozen=True, kw_only=True)
class LocalBlobGlobalAccess:
    digest: str
    mediaType: str
    ref: str
    size: int
    type: str


@dc(frozen=True, kw_only=True)
class LocalBlobAccess(Access):
    '''
    a blob that is accessible locally to the component-descriptor

    see: https://github.com/open-component-model/ocm-spec/blob/d74b6a210ff8c8c3486aa9b21e22c169d014806e/doc/04-extensions/01-extensions.md#localblob # noqa
    '''
    type: AccessTypeOrStr = AccessType.LOCAL_BLOB
    localReference: str
    size: int | None = None
    mediaType: str = 'application/data'
    referenceName: typing.Optional[str] = None
    globalAccess: typing.Optional[LocalBlobGlobalAccess | dict | None] = None


@dc(frozen=True, kw_only=True)
class OciAccess(Access):
    type: AccessType = AccessType.OCI_REGISTRY
    imageReference: str


@dc(frozen=True, kw_only=True)
class OciBlobAccess(OciAccess):
    type: AccessTypeOrStr = AccessType.OCI_BLOB
    mediaType: str
    digest: str
    size: int


@dc(frozen=True, kw_only=True)
class RelativeOciAccess(Access):
    reference: str
    type: AccessType = AccessType.RELATIVE_OCI_REFERENCE


@dc(frozen=True, kw_only=True)
class GithubAccess(Access):
    repoUrl: str
    ref: typing.Optional[str] = None
    commit: typing.Optional[str] = None
    type: AccessTypeOrStr = AccessType.GITHUB

    def __post_init__(self):
        parsed = self._normalise_and_parse_url()
        if not len(parsed.path[1:].split('/')):
            raise ValueError(f'{self.repoUrl=} must have exactly two path components')

    @functools.lru_cache
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


@dc(frozen=True, kw_only=True)
class S3Access(Access):
    bucketName: str
    objectKey: str
    region: typing.Optional[str] = None


class ArtefactType(enum.StrEnum):
    COSIGN_SIGNATURE = 'cosignSignature'
    GIT = 'git'
    OCI_IMAGE = 'ociImage'
    OCI_ARTEFACT = 'ociArtifact/v1'
    HELM_CHART = 'helmChart/v1'
    BLOB = 'blob/v1'
    DIRECTORY_TREE = 'directoryTree'


# hack: patch enum to accept "aliases"
# -> the values defined in enum above will be  used for serialisation; the aliases are also
# accepted for deserialisation
# note: the `/v1` suffix is _always_ optional (if absent, /v1 is implied)
ArtefactType._value2member_map_ |= {
    'blob': ArtefactType.BLOB,
    'git/v1': ArtefactType.GIT,
    'ociImage/v1': ArtefactType.OCI_IMAGE,
    'ociImage': ArtefactType.OCI_IMAGE,
    'helmChart': ArtefactType.HELM_CHART,
}


# aliases (deprecated!) for backwards-compatibility
SourceType = ArtefactType
ResourceType = ArtefactType


class ResourceRelation(enum.StrEnum):
    LOCAL = 'local'
    EXTERNAL = 'external'


@dc(frozen=True)
class Label:
    name: str
    value: typing.Union[str, int, float, bool, dict, list]


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
    ) -> typing.List[Label]:
        if self.find_label(name=label.name) and raise_if_present:
            raise ValueError(f'label {label.name} is already present')

        patched_labels = [l for l in self.labels if l.name != label.name]
        patched_labels.append(label)

        return dataclasses.replace(
            self,
            labels=patched_labels,
        )


@dc
class DigestSpec:
    hashAlgorithm: str
    normalisationAlgorithm: str
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
class Signature:
    name: str
    digest: DigestSpec
    signature: SignatureSpec


@dc(frozen=True)
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
    def identity(self, peers: typing.Sequence['Artifact']):
        '''
        returns the identity-object for this artifact (component-ref, resource, or source).

        Note that, in component-descriptor-v2, the `version` attribute is implicitly added iff
        there would otherwise be a conflict, iff this artifact only uses its `name` as
        identity-attr (which is the default).

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

        if len(identity) > 1:  # special-case-handling not required if there are additional-id-attrs
            return identity

        # check whether there are collissions
        for peer in peers:
            if peer is self:
                continue
            if peer.identity(peers=()) == identity:
                # there is at least one collision (id est: another artifact w/ same name)
                # pylint: disable=E1101
                return ArtifactIdentity(
                    name=self.name,
                    version=self.version,
                )
        # there were no collisions
        return identity


@dc(frozen=True)
class ComponentReference(Artifact, LabelMethodsMixin):
    name: str
    componentName: str
    version: str
    digest: typing.Optional[DigestSpec] = None
    extraIdentity: typing.Dict[str, str] = dataclasses.field(default_factory=dict)
    labels: typing.List[Label] = dataclasses.field(default_factory=tuple)


@dc(frozen=True)
class SourceReference(LabelMethodsMixin):
    identitySelector: dict[str, str]
    labels: list[Label] = dataclasses.field(default_factory=tuple)


@dc
class Resource(Artifact, LabelMethodsMixin):
    name: str
    version: str
    type: typing.Union[ArtefactType, str]
    access: typing.Union[
        # Order of types is important for deserialization. The first matching type will be taken,
        # i.e. keep generic accesses at the bottom of the list
        GithubAccess,
        LocalBlobAccess,
        OciBlobAccess,
        OciAccess,
        RelativeOciAccess,
        S3Access,
        dict,
        None,
    ]
    digest: typing.Optional[DigestSpec] = None
    extraIdentity: typing.Dict[str, str] = dataclasses.field(default_factory=dict)
    relation: ResourceRelation = ResourceRelation.LOCAL
    labels: typing.List[Label] = dataclasses.field(default_factory=tuple)
    srcRefs: list[SourceReference] = dataclasses.field(default_factory=tuple)

    def __post_init__(self):
        if dataclasses.is_dataclass(access := self.access):
            return

        if isinstance(access, dict):
            if not 'type' in access:
                raise ValueError('attribute `type` must be present')
            self.access = AccessDict(access)


@dc(frozen=True, kw_only=True)
class OcmRepository:
    type: AccessTypeOrStr


@dc(frozen=True, kw_only=True)
class OciOcmRepository(OcmRepository):
    baseUrl: str
    subPath: typing.Optional[str] = None
    type: AccessTypeOrStr = AccessType.OCI_REGISTRY

    @property
    def oci_ref(self):
        if not self.subPath:
            return self.baseUrl
        return f'{self.baseUrl.rstrip("/")}/{self.subPath.lstrip("/")}'

    def component_oci_ref(self, name: typing.Union[str, 'Component', 'ComponentIdentity'], /):
        if isinstance(name, Component):
            name = name.name
        elif isinstance(name, ComponentIdentity):
            name = name.name

        return '/'.join((
            self.oci_ref,
            'component-descriptors',
            name.lstrip('/').lower(), # oci-spec only allows lowercase
        ))

    def component_version_oci_ref(
        self,
        name: typing.Union[str, 'Component', 'ComponentIdentity'],
        version: str=None,
    ):
        if isinstance(name, Component):
            if not version:
                version = name.version
            name = name.name
        elif isinstance(name, ComponentIdentity):
            if not version:
                version = name.version
            name = name.name

        if not version:
            name, version = name.rsplit(':', 1)

        return f'{self.component_oci_ref(name)}:{version}'


# XXX remove type-aliases after renaming all usages of old names
RepositoryContext = OcmRepository
OciRepositoryContext = OciOcmRepository


@dc
class Source(Artifact, LabelMethodsMixin):
    name: str
    access: GithubAccess | dict
    version: typing.Optional[str] = None  # introduce this backwards-compatible for now
    extraIdentity: typing.Dict[str, str] = dataclasses.field(default_factory=dict)
    type: typing.Union[ArtefactType, str] = ArtefactType.GIT
    labels: typing.List[Label] = dataclasses.field(default_factory=list)

    def __post_init__(self):
        if dataclasses.is_dataclass(access := self.access):
            return

        if isinstance(access, dict):
            if not 'type' in access:
                raise ValueError('attribute `type` must be present')
            self.access = AccessDict(access)


# backwards-compatibility
ComponentSource = Source


@dc
class Component(LabelMethodsMixin):
    name: str     # must be valid URL w/o schema
    version: str  # relaxed semver

    repositoryContexts: typing.List[OciOcmRepository]
    provider: typing.Union[str, dict]

    sources: typing.List[Source]
    componentReferences: typing.List[ComponentReference]
    resources: typing.List[Resource]

    labels: typing.List[Label] = dataclasses.field(default_factory=list)

    creationTime: str | None = None

    @property
    def current_ocm_repo(self):
        if not self.repositoryContexts:
            return None
        return self.repositoryContexts[-1]

    @deprecated.deprecated
    def current_repository_ctx(self):
        if not self.repositoryContexts:
            return None
        return self.repositoryContexts[-1]

    def identity(self):
        return ComponentIdentity(name=self.name, version=self.version)

    def iter_artefacts(self) -> typing.Generator[None, None, Source | Resource]:
        if self.sources:
            yield from self.sources
        if self.resources:
            yield from self.resources


@functools.lru_cache
def _read_schema_file(schema_file_path: str):
    with open(schema_file_path) as f:
        return yaml.safe_load(f)


def enum_or_string(v, enum_type: enum.Enum):
    try:
        return enum_type(v)
    except ValueError:
        return str(v)


@dc
class ComponentDescriptor:
    meta: Metadata
    component: Component
    signatures: typing.List[Signature] = dataclasses.field(default_factory=list)

    @staticmethod
    def validate(
        component_descriptor_dict: dict,
        validation_mode: ValidationMode,
        json_schema_file_path: str = None,
    ):
        if validation_mode is ValidationMode.NONE:
            return

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
                raise NotImplementedError(validation_mode)

    @staticmethod
    def from_dict(
        component_descriptor_dict: dict,
        validation_mode: ValidationMode = ValidationMode.NONE,
    ):
        def dateparse(v):
            if not v:
                return None
            if isinstance(v, datetime.datetime):
                return v
            return dateutil.parser.isoparse(v)

        component_descriptor = dacite.from_dict(
            data_class=ComponentDescriptor,
            data=component_descriptor_dict,
            config=dacite.Config(
                cast=[
                    SchemaVersion,
                    ResourceRelation,
                ],
                type_hooks={
                    typing.Union[AccessType, str]: functools.partial(
                        enum_or_string, enum_type=AccessType
                    ),
                    typing.Union[ArtefactType, str]: functools.partial(
                        enum_or_string, enum_type=ArtefactType
                    ),
                    typing.Union[ArtifactIdentity, str]: functools.partial(
                        enum_or_string, enum_type=ArtefactType
                    ),
                    AccessType: functools.partial(
                        enum_or_string, enum_type=AccessType
                    ),
                    datetime.datetime: dateparse,
                },
            )
        )
        if not validation_mode is ValidationMode.NONE:
            ComponentDescriptor.validate(
                component_descriptor_dict=component_descriptor_dict,
                validation_mode=validation_mode,
            )

        return component_descriptor

    def to_fobj(self, fileobj: io.BytesIO):
        raw_dict = dataclasses.asdict(self)
        yaml.dump(
            data=raw_dict,
            stream=fileobj,
            Dumper=EnumValueYamlDumper,
        )


class EnumValueYamlDumper(yaml.SafeDumper):
    '''
    a yaml.SafeDumper that will dump enum objects using their values
    '''
    def represent_data(self, data):
        if isinstance(data, AccessDict):
            # yaml dumper won't know how to parse objects of type `AccessDict`
            # (altough it is just a wrapped dict) -> so convert it to a "real" dict
            data = dict(data)
        if isinstance(data, enum.Enum):
            return self.represent_data(data.value)
        return super().represent_data(data)
