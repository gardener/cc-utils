import collections

ProcessingJob = collections.namedtuple(
    'ProcessingJob',
    [
        'component',
        'container_image',
        'download_request',
        'upload_request',
        'upload_context_url',
    ]
)

ProcessingResources = collections.namedtuple(
    'ProcessingResources',
    ['resources', 'expected_count']
)
