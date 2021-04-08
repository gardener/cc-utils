import dataclasses

dc = dataclasses.dataclass


@dc
class ContainerImageDownloadRequest:
    source_ref: str
    target_file: str = None
    processing_callback: callable = None


@dc
class ContainerImageUploadRequest:
    source_ref: str
    target_ref: str
    source_file: str = None
    processing_callback: callable = None
