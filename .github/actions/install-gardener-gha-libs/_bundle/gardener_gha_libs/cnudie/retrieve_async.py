import warnings

import ocm.retrieve_async

# re-export classes and type aliases directly so isinstance checks and type annotations keep working
WriteBack = ocm.retrieve_async.WriteBack
ComponentDescriptorLookupById = ocm.retrieve_async.ComponentDescriptorLookupById
VersionLookupByComponent = ocm.retrieve_async.VersionLookupByComponent

_WARN = 'cnudie.retrieve_async is deprecated - use ocm.retrieve_async'


def in_memory_cache_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve_async.in_memory_cache_component_descriptor_lookup(*args, **kwargs)


def file_system_cache_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve_async.file_system_cache_component_descriptor_lookup(*args, **kwargs)


def delivery_service_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve_async.delivery_service_component_descriptor_lookup(*args, **kwargs)


async def component_descriptor_from_oci(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return await ocm.retrieve_async.component_descriptor_from_oci(*args, **kwargs)


def oci_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve_async.oci_component_descriptor_lookup(*args, **kwargs)


def version_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve_async.version_lookup(*args, **kwargs)


def composite_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve_async.composite_component_descriptor_lookup(*args, **kwargs)


def create_default_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve_async.create_default_component_descriptor_lookup(*args, **kwargs)


async def component_diff(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return await ocm.retrieve_async.component_diff(*args, **kwargs)


async def component_versions(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return await ocm.retrieve_async.component_versions(*args, **kwargs)
