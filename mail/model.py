import dataclasses


@dataclasses.dataclass
class Attachment:
    filename: str
    mimetype_main: str
    mimetype_sub: str
    bytes: bytes
