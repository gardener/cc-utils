import warnings

import ocm
import ocm.access
import ocm.diff
import ocm.util

import version as _version

# re-export classes and type aliases directly so isinstance checks and type annotations keep working
ComponentId = ocm.ComponentId
ComponentName = ocm.ComponentName

ComponentResource = ocm.diff.ComponentResource
LabelDiff = ocm.diff.LabelDiff
ComponentDiff = ocm.diff.ComponentDiff
ResourceDiff = ocm.diff.ResourceDiff

META_SEPARATOR = _version.META_SEPARATOR

_WARN = 'cnudie.util is deprecated - use ocm / ocm.access / ocm.diff / ocm.util / version instead'


def to_component_id(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.to_component_id(*args, **kwargs)


def to_component_name(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.to_component_name(*args, **kwargs)


def to_component(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.to_component(*args, **kwargs)


def iter_sorted(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    yield from ocm.iter_sorted(*args, **kwargs)


def to_component_id_and_repository_url(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.access.to_component_id_and_repository_url(*args, **kwargs)


def oci_ref(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.access.oci_ref(*args, **kwargs)


def target_oci_ref(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.access.target_oci_ref(*args, **kwargs)


def oci_artefact_reference(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.access.oci_artefact_reference(*args, **kwargs)


def normalise_component_name(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.access.normalise_component_name(*args, **kwargs)


def main_source(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.util.main_source(*args, **kwargs)


def determine_main_source_for_component(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.util.main_source(*args, **kwargs)


def diff_labels(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.diff.diff_labels(*args, **kwargs)


def diff_components(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.diff.diff_components(*args, **kwargs)


def diff_resources(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.diff.diff_resources(*args, **kwargs)


def format_component_diff(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return ocm.diff.format_component_diff(*args, **kwargs)


def sanitise_version(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return _version.sanitise_version(*args, **kwargs)


def desanitise_version(*args, **kwargs):
    warnings.warn(_WARN, DeprecationWarning, stacklevel=2)
    return _version.desanitise_version(*args, **kwargs)
