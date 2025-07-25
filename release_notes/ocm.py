'''
another opinionated / gardener-specific module for managing release-notes in
OCM-Component-Descriptors
'''

import collections.abc
import logging
import zlib

import cnudie.retrieve
import oci.client
import ocm
import ocm.gardener
import version as version_mod

logger = logging.getLogger(__name__)


release_notes_resource_name = 'release-notes'


def release_notes(
    component: ocm.ComponentIdentity | ocm.Component,
    oci_client: oci.client.Client,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup | None=None,
    absent_ok: bool=True,
) -> str | None:
    '''
    retrieves (raw, i.e. in markdown / text fmt) release-notes for the given component version.

    The release-notes are expected to be stored as a local-blob, and referenced from a resource
    named `release-notes`.

    If a full ocm.Component is passed-in, component_descriptor_lookup can be omitted (it is
    otherwise used to retrieve component-descriptor). Either way, the component-descriptor is
    expected to be stored in an OCI-Registry (hence the need for passing-in an oci-client).
    '''
    if not isinstance(component, ocm.Component):
        component = component_descriptor_lookup(component).component

    for resource in component.resources:
        if resource.name == release_notes_resource_name:
            break
    else:
        if absent_ok:
            return None
        raise ValueError(f'{component=} has no resource named `release-notes`')

    access = resource.access
    if not access.type is ocm.AccessType.LOCAL_BLOB:
        raise ValueError(f'do not know how to handle {access.type=} ({component=})')
    access: ocm.LocalBlobAccess

    oci_ref = component.current_ocm_repo.component_version_oci_ref(
        name=component.name,
        version=component.version,
    )

    release_notes_blob = oci_client.blob(
        image_reference=oci_ref,
        digest=access.localReference,
    )

    if access.mediaType.endswith('/gzip'):
        release_notes_bytes = zlib.decompress(release_notes_blob.content, wbits=31)
    else:
        release_notes_bytes = release_notes_blob.content

    return release_notes_bytes.decode('utf-8')


def release_notes_markdown_with_heading(
    component_id: ocm.ComponentIdentity,
    release_notes: str,
) -> str:
    header = f'[{component_id.name}:{component_id.version}]'
    return f'{header}\n{release_notes}'


def release_notes_range(
    version_vector: ocm.gardener.UpgradeVector,
    versions: collections.abc.Iterable[version_mod.Version],
    oci_client: oci.client.Client,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup | None=None,
    absent_ok: bool=True,
) -> collections.abc.Iterable[tuple[ocm.ComponentIdentity, str]]:
    '''
    yields pairs of component-id and release-notes in specified range,
    excluding release-notes for `whence`-version,
    including release-notes for `whither`-version.
    '''
    versions_in_range = version_mod.iter_upgrade_path(
        whence=version_vector.whence.version,
        whither=version_vector.whither.version,
        versions=versions,
    )

    for version in versions_in_range:
        component_id = ocm.ComponentIdentity(
            name=version_vector.component_name,
            version=version,
        )
        logger.info(f'retrieving release-notes for {component_id=}')
        notes = release_notes(
           component=component_id,
           oci_client=oci_client,
           component_descriptor_lookup=component_descriptor_lookup,
           absent_ok=absent_ok,
        )

        if not notes: # previous call would already have failed, if absent_ok were falsy
            logger.info(f'did not find release-notes for {component_id=}')
            continue

        logger.info(f'found {len(notes)=} characters of release-notes for {component_id=}')
        yield component_id, notes


def release_notes_range_recursive(
    version_vector: ocm.gardener.UpgradeVector,
    component_descriptor_lookup: ocm.ComponentDescriptorLookup,
    version_lookup: ocm.VersionLookup,
    oci_client: oci.client.Client,
    version_filter=lambda v: True,
    whither_component=None,
) -> collections.abc.Iterable[tuple[ocm.ComponentIdentity, str]]:
    '''
    recursively retrieves release-notes for the given version-vector. Yields pairs of
    component-id and corresponding release-notes.
    '''
    whence_component = component_descriptor_lookup(version_vector.whence).component
    if not whither_component:
        whither_component = component_descriptor_lookup(version_vector.whither).component
    component_diff = cnudie.retrieve.component_diff(
        left_component=whence_component,
        right_component=whither_component,
        component_descriptor_lookup=component_descriptor_lookup,
    )

    for whence_component, whither_component in component_diff.cpairs_version_changed:
        versions = [v for v in version_lookup(whence_component) if version_filter(v)]

        version_vector = ocm.gardener.UpgradeVector(
            whence=whence_component,
            whither=whither_component,
        )
        if version_vector.is_downgrade:
            logger.warn(f'skipping retrieval of release-notes for downgrade: {version_vector=}')
            continue
        yield from release_notes_range(
            version_vector=version_vector,
            versions=versions,
            oci_client=oci_client,
            component_descriptor_lookup=component_descriptor_lookup,
            absent_ok=True,
        )
