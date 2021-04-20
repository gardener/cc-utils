import dataclasses
import enum
import typing

import requests

OCI_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.oci.image.manifest.v1+json'
DOCKER_MANIFEST_SCHEMA_V2_MIME = 'application/vnd.docker.distribution.manifest.v2+json'
empty_dict = dataclasses.field(default_factory=dict)


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
