import collections.abc
import dataclasses
import enum
import functools
import json
import typing
import urllib.parse

import dacite

import oci.util

OCI_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.oci.image.manifest.v1+json'
OCI_IMAGE_INDEX_MIME = 'application/vnd.oci.image.index.v1+json'

DOCKER_MANIFEST_LIST_MIME = 'application/vnd.docker.distribution.manifest.list.v2+json'
DOCKER_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.docker.distribution.manifest.v2+json'

empty_dict = dataclasses.field(default_factory=dict) # noqa:E3701


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
    GAR = 'gar'
    GCR = 'gcr'
    DOCKERHUB = 'dockerhub'
    ARTIFACTORY = 'artifactory'
    GHCR = 'ghcr'
    ALIYUN = 'aliyun'
    UNKNOWN = 'unknown'

    @staticmethod
    def from_image_ref(
        image_reference: typing.Union[str, 'OciImageReference'],
    ) -> 'OciRegistryType':
        netloc = OciImageReference.to_image_ref(image_reference=image_reference).netloc

        if 'pkg.dev' in netloc:
            return OciRegistryType.GAR
        if 'gcr.io' in netloc:
            return OciRegistryType.GCR
        if 'docker.io' in netloc:
            return OciRegistryType.DOCKERHUB
        if 'ghcr.io' in netloc:
            return OciRegistryType.GHCR
        if 'cr.aliyuncs.com' in netloc:
            return OciRegistryType.ALIYUN

        return OciRegistryType.UNKNOWN


class OciImageReference:
    @staticmethod
    def to_image_ref(
        image_reference: typing.Union[str, 'OciImageReference'],
        normalise: bool=True,
    ):
        if isinstance(image_reference, OciImageReference):
            return image_reference
        else:
            return OciImageReference(
                image_reference=image_reference,
                normalise=normalise,
            )

    def __init__(
        self,
        image_reference: typing.Union[str, 'OciImageReference'],
        normalise: bool=True,
    ):
        if isinstance(image_reference, OciImageReference):
            self._orig_image_reference = image_reference._orig_image_reference
        elif isinstance(image_reference, str):
            self._orig_image_reference = image_reference
        else:
            raise ValueError(image_reference)
        self._normalise = normalise

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
        name = p.netloc + p.path.rsplit('@', 1)[0].rsplit(':', 1)[0]

        return name

    @property
    @functools.cache
    def name(self) -> str:
        '''
        returns the (normalised) image name (omitting api-prefix and tag)
        '''
        p = self.urlparsed
        name = p.path[1:].rsplit('@', 1)[0].rsplit(':', 1)[0]

        return name

    @property
    @functools.cache
    def has_digest_tag(self) -> bool:
        return self.tag_type is OciTagType.DIGEST

    @property
    @functools.cache
    def has_symbolical_tag(self) -> bool:
        return self.tag_type is OciTagType.SYMBOLIC

    @property
    @functools.cache
    def has_mixed_tag(self) -> bool:
        if not self.has_digest_tag:
            return False

        p = self.urlparsed
        ref_without_digest_tag = p.netloc + p.path.rsplit('@', 1)[0]

        return ':' in ref_without_digest_tag

    @property
    @functools.cache
    def with_symbolical_tag(self) -> 'OciImageReference':
        if not (self.has_symbolical_tag or self.has_mixed_tag):
            raise ValueError(f'does not contain a symbolical tag: {str(self)=}')

        p = self.urlparsed
        return OciImageReference(
            image_reference=p.netloc + p.path.rsplit('@', 1)[0],
            normalise=self._normalise,
        )

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
    def parsed_mixed_tag(self) -> tuple[str, str]:
        if not self.has_mixed_tag:
            raise ValueError(f'not a mixed-tag: {str(self)=}')

        p = self.urlparsed
        digest_tag = p.path.rsplit('@', 1)[-1]
        symbolical_tag = p.path.rsplit('@', 1)[0].rsplit(':', 1)[-1]
        return symbolical_tag, digest_tag

    @property
    def parsed_digest_tag(self) -> tuple[str, str]:
        if not self.tag_type is OciTagType.DIGEST:
            raise ValueError(f'not a digest-tag: {str(self)=}')

        algorithm, digest = self.tag.split(':')
        return algorithm, digest

    @property
    def digest(self) -> str:
        _, digest = self.parsed_digest_tag
        return digest

    @property
    def local_ref(self) -> str:
        return self.urlparsed.path.removeprefix('/')

    @property
    @functools.cache
    def urlparsed(self) -> urllib.parse.ParseResult:
        if not '://' in (img_ref := str(self)) and not img_ref.startswith('/'):
            return urllib.parse.urlparse(f'https://{img_ref}')
        return urllib.parse.urlparse(img_ref)

    def with_tag(self, tag: str) -> 'OciImageReference':
        if 'sha256' in tag and not '@' in tag:
            image_ref = f'{self.ref_without_tag}@{tag}'
        else:
            image_ref = f'{self.ref_without_tag}:{tag}'

        return OciImageReference(
            image_reference=image_ref,
            normalise=self._normalise,
        )

    def __str__(self) -> str:
        if self._normalise:
            return self.normalised_image_reference
        return self._orig_image_reference

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


class OciImageNotFoundException(Exception):
    pass


@dataclasses.dataclass(kw_only=True)
class OciBlobRef:
    digest: str
    mediaType: str
    size: int
    annotations: dict | None = None

    def as_dict(self) -> dict:
        raw = dataclasses.asdict(self)
        # fields that are None should not be included in the output
        raw = {k:v for k,v in raw.items() if v is not None}
        return raw

    def __hash__(self):
        annotations = tuple(sorted(self.annotations.items())) if self.annotations else ()
        return hash((self.digest, self.size, self.mediaType, annotations))

    def __eq__(self, other) -> bool:
        if not isinstance(other, OciBlobRef):
            return False

        other: OciBlobRef

        if not self.digest == other.digest:
            return False

        if not self.mediaType == other.mediaType:
            return False

        if not self.size == other.size:
            return False

        if not self.annotations == other.annotations:
            return False

        return True


@dataclasses.dataclass
class OciImageManifest:
    config: OciBlobRef
    layers: collections.abc.Sequence[OciBlobRef]
    mediaType: str = OCI_MANIFEST_SCHEMA_V2_MIME
    schemaVersion: int = 2
    annotations: dict = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict:
        def layer_to_dict(layer):
            if isinstance(layer, dict):
                return layer
            else:
                return layer.as_dict()

        return {
            'config': self.config.as_dict(),
            'layers': [layer_to_dict(layer) for layer in self.layers],
            'mediaType': self.mediaType,
            'schemaVersion': self.schemaVersion,
            'annotations': self.annotations,
        }

    def blobs(self) -> collections.abc.Generator[OciBlobRef, None, None]:
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
    fsLayers: list[OciBlobRefV1]
    history: list[dict] # don't care about details
    signatures: list[dict] = empty_dict # don't care about details
    schemaVersion: int = 1
    layers = None # to be initialised by factory-function

    def blobs(self) -> collections.abc.Generator[OciBlobRef, None, None]:
        if not self.layers:
            raise ValueError('instance was not properly initialised')

        yield from self.layers # noqa


@dataclasses.dataclass(frozen=True)
class OciPlatform:
    '''
    https://github.com/distribution/distribution/blob/main/docs/spec/manifest-v2-2.md#manifest-list
    '''
    architecture: str
    os: str # could also be a dict (see spec)
    variant: str | None = None
    features: list[str] | None = None

    def as_dict(self) -> dict:
        # need custom serialisation, because some OCI registries do not like null-values
        # (must be absent instead)
        raw = dataclasses.asdict(self)

        if not self.variant:
            del raw['variant']

        if not self.features:
            del raw['features']

        return raw

    def __eq__(self, other):
        if not isinstance(other, OciPlatform):
            return False

        if not self.architecture == other.architecture:
            return False

        if not self.os == other.os:
            return False

        return True


@dataclasses.dataclass
class OciImageManifestListEntry(OciBlobRef):
    artifactType: str | None = None
    data: str | None = None
    platform: OciPlatform | None = None
    urls: list[str] | None = None

    def as_dict(self) -> dict:
        raw = OciBlobRef.as_dict(self)
        # platform is an optional attribute according to oci spec
        # => only include in the output if set
        if self.platform:
            raw['platform'] = self.platform.as_dict()
        # fields that are None should not be included in the output
        raw = {k:v for k,v in raw.items() if v is not None}
        return raw


@dataclasses.dataclass
class OciImageManifestList:
    '''Covers both Docker Manifest List
        (https://github.com/distribution/distribution/blob/main/docs/spec/manifest-v2-2.md#manifest-list)
        and OCI Image Index
        (https://github.com/opencontainers/image-spec/blob/main/image-index.md)
    '''
    manifests: list[OciImageManifestListEntry]
    mediaType: str = OCI_IMAGE_INDEX_MIME
    schemaVersion: int = 2
    annotations: dict = dataclasses.field(default_factory=dict)

    def as_dict(self):
        raw = {
            'manifests': [le.as_dict() for le in self.manifests],
            'mediaType': self.mediaType,
            'schemaVersion': self.schemaVersion,
        }

        if self.mediaType == OCI_IMAGE_INDEX_MIME:
            raw['annotations'] = self.annotations

        return raw


def as_manifest(
    manifest: str | bytes | dict | OciImageManifest | OciImageManifestList,
) -> OciImageManifest | OciImageManifestList:
    '''
    returns a deserialised equivalent of the passed-in manifest. For convenience, if passed-in
    manifest is already an instance of either OciImageManifest or OciImageManifestList, the
    passed value is returned unchanged.
    '''
    if isinstance(manifest, (OciImageManifest, OciImageManifestList)):
        return manifest

    if isinstance(manifest, (str, bytes)):
        manifest = json.loads(manifest)

    if (media_type := manifest.get('mediaType')) in (
        DOCKER_MANIFEST_LIST_MIME,
        OCI_IMAGE_INDEX_MIME,
    ):
        return dacite.from_dict(
            data_class=OciImageManifestList,
            data=manifest,
        )

    if media_type in (
        DOCKER_MANIFEST_SCHEMA_V2_MIME,
        OCI_MANIFEST_SCHEMA_V2_MIME,
    ):
        return dacite.from_dict(
            data_class=OciImageManifest,
            data=manifest,
        )

    raise ValueError(manifest, 'unknown schema-version')
