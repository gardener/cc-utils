import warnings

import ocm
import ocm.retrieve

# re-export classes and type aliases directly so isinstance checks and type annotations keep working
OcmRepositoryMappingEntry = ocm.retrieve.OcmRepositoryMappingEntry
WriteBack = ocm.retrieve.WriteBack
OcmRepositoryCfg = ocm.retrieve.OcmRepositoryCfg

# backwards-compat aliases (were already re-exports in the original module)
ComponentName = ocm.ComponentName
OcmRepositoryLookup = ocm.OcmRepositoryLookup
ComponentDescriptorLookupById = ocm.ComponentDescriptorLookup
VersionLookupByComponent = ocm.VersionLookup

_WARN = 'cnudie.retrieve is deprecated - use ocm.retrieve'


def iter_ocm_repositories(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    yield from ocm.retrieve.iter_ocm_repositories(*args, **kwargs)


def ocm_repository_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.ocm_repository_lookup(*args, **kwargs)


def in_memory_cache_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.in_memory_cache_component_descriptor_lookup(*args, **kwargs)


def file_system_cache_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.file_system_cache_component_descriptor_lookup(*args, **kwargs)


def delivery_service_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.delivery_service_component_descriptor_lookup(*args, **kwargs)


def component_descriptor_from_oci(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.component_descriptor_from_oci(*args, **kwargs)


def oci_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.oci_component_descriptor_lookup(*args, **kwargs)


def error_code_indicating_not_found(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.error_code_indicating_not_found(*args, **kwargs)


def version_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.version_lookup(*args, **kwargs)


def composite_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.composite_component_descriptor_lookup(*args, **kwargs)


def create_default_component_descriptor_lookup(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.create_default_component_descriptor_lookup(*args, **kwargs)


def component_diff(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.retrieve.component_diff(*args, **kwargs)
