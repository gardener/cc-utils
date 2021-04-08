import dataclasses
dc = dataclasses.dataclass


@dc
class ProcessingJob:
    component: object
    container_image: object
    download_request: object
    upload_request: object
    upload_context_url: object


@dc
class ProcessingResources:
    resources: object
    expected_count: int
