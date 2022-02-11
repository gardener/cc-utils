import dataclasses
import enum
import functools
import typing
import urllib.parse

import requests

import oci.util

OCI_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.oci.image.manifest.v1+json'
OCI_IMAGE_INDEX_MIME = 'application/vnd.oci.image.index.v1+json'

DOCKER_MANIFEST_LIST_MIME = 'application/vnd.docker.distribution.manifest.list.v2+json'
DOCKER_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.docker.distribution.manifest.v2+json'

empty_dict = dataclasses.field(default_factory=dict)


class MimeTypes:
    '''
    predefined, well-known mimetypes, handy to be used in oci.client.Client.manifest as `accept` arg

    single_image: force single-image
    multiarch: force multi-arch (image-list)
    prefer_multiarch

    note: not all registries honour `access` header
    '''
    single_image = ', '.join((OCI_MANIFEST_SCHEMA_V2_MIME, DOCKER_MANIFEST_SCHEMA_V2_MIME))
    multiarch = ', '.join((OCI_IMAGE_INDEX_MIME, DOCKER_MANIFEST_LIST_MIME))
    prefer_multiarch = ', '.join((multiarch, single_image))


class OciTagType(enum.Enum):
    SYMBOLIC = 'symbolic'
    DIGEST = 'digest'
    NO_TAG = 'no_tag'


class OciRegistryType(enum.Enum):
    GCR = 'gcr'
    DOCKERHUB = 'dockerhub'
    ARTIFACTORY = 'artifactory'
    UNKNOWN = 'unknown'


class OciImageReference:
    @staticmethod
    def to_image_ref(image_reference: typing.Union[str, 'OciImageReference']):
        if isinstance(image_reference, OciImageReference):
            return image_reference
        else:
            return OciImageReference(image_reference=image_reference)

    def __init__(self, image_reference: typing.Union[str, 'OciImageReference']):
        if isinstance(image_reference, OciImageReference):
            self._orig_image_reference = image_reference._orig_image_reference
        elif isinstance(image_reference, str):
            self._orig_image_reference = image_reference
        else:
            raise ValueError(image_reference)

    @property
    def original_image_reference(self) -> str:
        return self._orig_image_reference

    @property
    @functools.cache
    def normalised_image_reference(self) -> str:
        return oci.util.normalise_image_reference(self._orig_image_reference)

    @property
    @functools.cache
    def netloc(self) -> str:
        return self.urlparsed.netloc

    @property
    @functools.cache
    def ref_without_tag(self) -> str:
        '''
        returns the (normalised) image reference w/o the tag or digest tag.
        '''
        p = self.urlparsed
        name = p.netloc + p.path.rsplit(':', 1)[0].rsplit('@', 1)[0]

        return name

    @property
    @functools.cache
    def name(self) -> str:
        '''
        returns the (normalised) image name (omitting api-prefix and tag)
        '''
        p = self.urlparsed
        name = p.path[1:].rsplit(':', 1)[0].rsplit('@', 1)[0]

        return name

    @property
    @functools.cache
    def has_digest_tag(self) -> bool:
        if self.tag_type is OciTagType.DIGEST:
            return True
        else:
            return False

    @property
    @functools.cache
    def has_symbolical_tag(self) -> bool:
        if self.tag_type is OciTagType.SYMBOLIC:
            return True
        else:
            return False

    @property
    @functools.cache
    def has_tag(self):
        return not self.tag_type is OciTagType.NO_TAG

    @property
    @functools.cache
    def tag(self) -> str:
        p = self.urlparsed

        if '@' in p.path:
            return p.path.rsplit('@', 1)[-1]
        elif ':' in p.path:
            return p.path.rsplit(':', 1)[-1]
        else:
            raise ValueError(f'no tag found for {str(self)}')

    @property
    @functools.cache
    def tag_type(self) -> OciTagType:
        p = self.urlparsed

        if '@' in p.path:
            return OciTagType.DIGEST
        elif ':' in p.path:
            return OciTagType.SYMBOLIC
        else:
            return OciTagType.NO_TAG

    @property
    def parsed_digest_tag(self) -> typing.Tuple[str, str]:
        if not self.tag_type is OciTagType.DIGEST:
            raise ValueError(f'not a digest-tag: {str(self)=}')

        algorithm, digest = self.tag.split(':')
        return algorithm, digest

    @property
    @functools.cache
    def urlparsed(self) -> urllib.parse.ParseResult:
        if not '://' in (img_ref := str(self)):
            return urllib.parse.urlparse(f'https://{img_ref}')
        return urllib.parse.urlparse(img_ref)

    def __str__(self) -> str:
        return self.normalised_image_reference

    def __repr__(self) -> str:
        return f'OciImageReference({str(self)})'

    def __eq__(self, other) -> bool:
        if not isinstance(other, OciImageReference):
            # XXX: should we return True for str with same value?
            return False

        if self._orig_image_reference == other._orig_image_reference:
            return True

        return oci.util.normalise_image_reference(self._orig_image_reference) == \
               oci.util.normalise_image_reference(other._orig_image_reference)

    def __hash__(self):
        return hash((self._orig_image_reference,))


class OciManifestSchemaVersion(enum.Enum):
    V1 = 1
    V2 = 2


class OciImageNotFoundException(requests.exceptions.HTTPError):
    pass


@dataclasses.dataclass(frozen=True)
class OciBlobRef:
    digest: str
    mediaType: str
    size: int


@dataclasses.dataclass
class OciImageManifest:
    config: OciBlobRef
    layers: typing.Sequence[OciBlobRef]
    mediaType: str = OCI_MANIFEST_SCHEMA_V2_MIME
    schemaVersion: int = 2

    def blobs(self) -> typing.Sequence[OciBlobRef]:
        yield self.config
        yield from self.layers


@dataclasses.dataclass
class OciBlobRefV1:
    blobSum: str


@dataclasses.dataclass
class OciImageManifestV1:
    '''
    defines replication-relevant parts of the (deprecated) oci-manifest-schema version 1

    note: the `layers` attr must be initialised after deserialisation with v2-compatible
    objects of `OciBlobRef` (see oci/client.py for reference)

    this class is not intended to be instantiated by users of this module
    '''
    name: str
    tag: str
    architecture: str
    fsLayers: typing.List[OciBlobRefV1]
    history: typing.List[typing.Dict] # don't care about details
    signatures: typing.List[typing.Dict] = empty_dict # don't care about details
    schemaVersion: int = 1
    layers = None # to be initialised by factory-function

    def blobs(self) -> typing.Sequence[OciBlobRef]:
        if not self.layers:
            raise ValueError('instance was not properly initialised')

        yield from self.layers


@dataclasses.dataclass(frozen=True)
class OciPlatform:
    '''
    https://github.com/distribution/distribution/blob/main/docs/spec/manifest-v2-2.md#manifest-list
    '''
    architecture: str
    os: str # could also be a dict (see spec)
    variant: typing.Optional[str] = None
    features: typing.Optional[list[str]] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        # need custom serialisation, because some OCI registries do not like null-values
        # (must be absent instead)
        raw = dataclasses.asdict(self)

        if not self.variant:
            del raw['variant']

        return raw


@dataclasses.dataclass(frozen=True)
class OciImageManifestListEntry(OciBlobRef):
    platform: OciPlatform

    def as_dict(self) -> dict:
        raw = dataclasses.asdict(self)
        raw['platform'] = self.platform.as_dict()
        return raw


@dataclasses.dataclass
class OciImageManifestList:
    manifests: typing.List[OciImageManifestListEntry]
    mediaType: str = DOCKER_MANIFEST_LIST_MIME
    schemaVersion: int = 2

    def as_dict(self):
        return {
            'manifests': [le.as_dict() for le in self.manifests],
            'mediaType': self.mediaType,
            'schemaVersion': self.schemaVersion,
        }
