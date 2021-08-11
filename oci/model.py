import dataclasses
import enum
import functools
import typing
import urllib.parse

import requests

import oci.util

OCI_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.oci.image.manifest.v1+json'
DOCKER_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.docker.distribution.manifest.v2+json'
empty_dict = dataclasses.field(default_factory=dict)


class OciTagType(enum.Enum):
    SYMBOLIC = 'symbolic'
    DIGEST = 'digest'


class OciImageReference:
    def __init__(self, image_reference: str):
        self._orig_image_reference = image_reference

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
    def name(self) -> str:
        '''
        returns the (normalised) image name, i.e. the image reference w/o the tag or digest tag.
        '''
        p = self.urlparsed
        name = p.netloc + p.path.rsplit(':', 1)[0].rsplit('@', 1)[0]

        return name

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
            raise ValueError(f'failed to determine tag-type for {str(self)=}')

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
