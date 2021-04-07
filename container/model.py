import collections

'''
this module is deprecated - do not use
'''

ContainerImageDownloadRequest = collections.namedtuple(
    'ContainerImageDownloadRequest',
    ['source_ref', 'target_file'],
    defaults=[None]
)


ContainerImageUploadRequest = collections.namedtuple(
    'ContainerImageUploadRequest',
    ['source_ref', 'source_file', 'target_ref', 'processing_callback'],
    defaults=[None]
)
