import dataclasses
import typing


@dataclasses.dataclass
class ContainerImageUploadRequest:
    '''
    represents an image upload request (with optional processing, e.g. for removing files)
    '''
    source_ref: str
    target_ref: str
    processing_callback: typing.Callable[[str, str], None] = None # called with (in_fh, out_fh)
