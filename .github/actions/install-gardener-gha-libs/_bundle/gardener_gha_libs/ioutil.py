import dataclasses
import typing


@dataclasses.dataclass
class BlobDescriptor:
    content: typing.Generator[bytes, None, None]
    size: int
    name: str = None
