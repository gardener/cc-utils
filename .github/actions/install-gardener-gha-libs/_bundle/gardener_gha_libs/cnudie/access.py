import warnings

import ocm.access


def s3_access_as_blob_descriptor(*args, **kwargs):
    warnings.warn(
        'cnudie.access is deprecated - use ocm.access',
        DeprecationWarning,
        stacklevel=2,
    )
    return ocm.access.s3_access_as_blob_descriptor(*args, **kwargs)


def access_to_digest_lookup(*args, **kwargs):
    warnings.warn(
        'cnudie.access is deprecated - use ocm.access',
        DeprecationWarning,
        stacklevel=2,
    )
    return ocm.access.access_to_digest_lookup(*args, **kwargs)
