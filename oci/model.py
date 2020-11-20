import dataclasses
import typing

# XXX rm dependencies towards containerregistry package
from containerregistry.client.v2_2 import docker_http


class OciImageNotFoundException(Exception):
    pass


@dataclasses.dataclass
class OciBlobRef:
    digest: str
    mediaType: str
    size: int


@dataclasses.dataclass
class OciImageManifest:
    config: OciBlobRef
    layers: typing.Sequence[OciBlobRef]
    mediaType: str = docker_http.MANIFEST_SCHEMA2_MIME
    schemaVersion: int = 2
