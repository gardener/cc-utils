import dataclasses
import typing


optional_str = typing.Optional[str]


@dataclasses.dataclass
class OperatingSystemId:
    '''
    Operating System identification, as specified in:
    https://www.freedesktop.org/software/systemd/man/os-release.html
    '''

    NAME: optional_str = None
    ID: optional_str = None
    PRETTY_NAME: optional_str = None
    CPE_NAME: optional_str = None
    VARIANT: optional_str = None
    VARIANT_ID: optional_str = None
    VERSION: optional_str = None
    VERSION_ID: optional_str = None
    VERSION_CODENAME: optional_str = None
    BUILD_ID: optional_str = None
    IMAGE_ID: optional_str = None
    IMAGE_VERSION: optional_str = None
