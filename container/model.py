import collections

ContainerImageDownloadRequest = collections.namedtuple(
    'ContainerImageDownloadRequest',
    ['source_ref', 'target_file'],
    defaults=[None]
)

'''
processing_callback: callable; called with (in_fh, out_fh)
'''

ContainerImageUploadRequest = collections.namedtuple(
    'ContainerImageUploadRequest',
    ['source_ref', 'source_file', 'target_ref', 'processing_callback'],
    defaults=[None]
)
