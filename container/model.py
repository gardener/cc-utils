import collections

'''
processing_callback: callable; called with (in_fh, out_fh)
'''
ContainerImageUploadRequest = collections.namedtuple(
    'ContainerImageUploadRequest',
    ['source_ref', 'target_ref', 'processing_callback'],
    defaults=[None],
)
