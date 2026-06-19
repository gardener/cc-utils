# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Iteration and retrieval utilities for SBOM documents attached to OCM resources.

Yield order — callers may use early-exit to avoid OCI I/O:
  1. SbomSource.OCM      — matched from the component descriptor in-memory; no network I/O.
  2. SbomSource.OCI_REFERRER — queried live from the OCI referrers API; only emitted when
                               `oci_client` is provided and the resource is OCI-addressable.
'''
import collections.abc
import dataclasses
import enum
import logging

import oci.client as oc
import oci.model as om
import ocm
import ocm.iter as ocm_iter
import sbom.oci as soci

logger = logging.getLogger(__name__)


class SbomSource(enum.StrEnum):
    OCM          = 'ocm'
    OCI_REFERRER = 'oci-referrer'


@dataclasses.dataclass
class SbomMapping:
    '''
    Associates one SBOM artefact with the payload resource it describes.

    `source` indicates how the SBOM was discovered:
      OCM          — `sbom` is an ocm.Resource from the component descriptor.
      OCI_REFERRER — `sbom` is an OciReferrer descriptor from the referrers API.

    `component` is included so callers can resolve LocalBlobAccess references back to
    the correct OCI repository (via component.current_ocm_repo).
    '''
    source:    SbomSource
    component: ocm.Component
    resource:  ocm.Resource           # payload resource the SBOM describes
    sbom:      ocm.Resource | om.OciReferrer


_SBOM_MEDIA_TYPES = frozenset(mt for _, mt in soci.SBOM_FORMATS)


def _is_sbom_resource(resource: ocm.Resource) -> bool:
    return resource.type in _SBOM_MEDIA_TYPES


def _oci_ref_for_resource(resource: ocm.Resource) -> str | None:
    '''Return the OCI image reference for a resource, or None if not OCI-addressable.'''
    access = resource.access
    if isinstance(access, ocm.OciAccess):
        return access.imageReference
    return None


def iter_sboms_for_resource(
    resource: ocm.Resource,
    component: ocm.Component,
    oci_client: oc.Client | None = None,
    ignore_unsupported: bool = True,
) -> collections.abc.Generator['SbomMapping', None, None]:
    '''
    Yield SbomMapping entries for all SBOM artefacts associated with the given resource.

    Yield order (see module docstring):
      1. OCM-registered SBOM resources (no I/O).
      2. OCI referrer manifests (requires oci_client; skipped otherwise).

    @param resource:           the payload resource to find SBOMs for
    @param component:          the component containing the resource (needed to resolve
                               LocalBlobAccess refs and for OCI repo derivation)
    @param oci_client:         if provided, also query the OCI referrers API
    @param ignore_unsupported: if True, silently skip resources whose access type cannot
                               be resolved to an OCI reference (relevant for OCI_REFERRER
                               path); if False, raise ValueError instead
    '''
    # --- phase 1: OCM resources from the component descriptor (in-memory, no I/O) ---
    resource_name = resource.name
    # SBOM resources share name (and non-sbom extraIdentity keys) with their source resource
    source_extra = {
        k: v for k, v in (resource.extraIdentity or {}).items()
        if k not in ('sbom-format', 'version')
    }
    for candidate in component.resources:
        if not _is_sbom_resource(candidate):
            continue
        if candidate.name != resource_name:
            continue
        # platform keys from source must be present in candidate extraIdentity
        candidate_extra = candidate.extraIdentity or {}
        if any(candidate_extra.get(k) != v for k, v in source_extra.items()):
            continue
        yield SbomMapping(
            source=SbomSource.OCM,
            component=component,
            resource=resource,
            sbom=candidate,
        )

    # --- phase 2: OCI referrers (live I/O) ---
    if oci_client is None:
        return

    image_ref = _oci_ref_for_resource(resource)
    if image_ref is None:
        if not ignore_unsupported:
            raise ValueError(
                f'resource {resource.name!r} access type {type(resource.access).__name__!r} '
                'cannot be resolved to an OCI reference; '
                'pass ignore_unsupported=True to skip silently'
            )
        return

    for _, media_type in soci.SBOM_FORMATS:
        referrers = oci_client.referrers(
            image_reference=image_ref,
            artifact_type=media_type,
            absent_ok=True,
        )
        if not referrers:
            continue
        for ref in referrers:
            yield SbomMapping(
                source=SbomSource.OCI_REFERRER,
                component=component,
                resource=resource,
                sbom=ref,
            )


def iter_sboms(
    component: ocm.Component | ocm.ComponentDescriptor,
    lookup: ocm.ComponentDescriptorLookup,
    oci_client: oc.Client | None = None,
    ignore_unsupported: bool = True,
    **kwargs,
) -> collections.abc.Generator['SbomMapping', None, None]:
    '''
    Yield SbomMapping entries for all resources in the transitive component tree.

    Iterates the full component tree via ocm.iter.iter_resources and calls
    iter_sboms_for_resource for each resource node.  All kwargs are forwarded
    to ocm.iter.iter_resources (e.g. recursion_depth, component_filter).

    Yield order within each resource follows iter_sboms_for_resource (OCM first,
    then OCI referrers).
    '''
    for node in ocm_iter.iter_resources(component, lookup, **kwargs):
        yield from iter_sboms_for_resource(
            resource=node.resource,
            component=node.component,
            oci_client=oci_client,
            ignore_unsupported=ignore_unsupported,
        )


def fetch_sbom_document(
    mapping: 'SbomMapping',
    oci_client: oc.Client,
) -> bytes:
    '''
    Fetch and return the raw SBOM document bytes for the given SbomMapping.

    Delegates to the appropriate retrieval path based on mapping.source:

      OCM / OciAccess:
        The sbom resource's imageReference points at a referrer manifest digest.
        Fetch the manifest, then retrieve layer[0] blob.

      OCM / LocalBlobAccess:
        The sbom resource's localReference is a blob digest stored in the component's
        OCI repository (derived from component.current_ocm_repo).
        Fetch the blob directly.

      OCI_REFERRER:
        The OciReferrer.digest identifies the referrer manifest.
        Fetch the manifest from the payload resource's repository, then retrieve layer[0].
    '''
    sbom = mapping.sbom

    if mapping.source is SbomSource.OCM:
        access = sbom.access
        if isinstance(access, ocm.OciAccess):
            return _fetch_blob_from_referrer_manifest(
                repo_and_digest=access.imageReference,
                oci_client=oci_client,
            )
        if isinstance(access, ocm.LocalBlobAccess):
            repo = mapping.component.current_ocm_repo.component_oci_ref(mapping.component)
            return oci_client.blob(
                image_reference=repo,
                digest=access.localReference,
            ).content
        raise ValueError(
            f'unsupported access type for SBOM fetch: {type(access).__name__!r}'
        )

    # SbomSource.OCI_REFERRER
    image_ref = _oci_ref_for_resource(mapping.resource)
    if image_ref is None:
        raise ValueError(
            f'cannot fetch OCI referrer SBOM: resource {mapping.resource.name!r} '
            'has no OCI-addressable access'
        )
    repo = om.OciImageReference.to_image_ref(image_ref).ref_without_tag
    return _fetch_blob_from_referrer_manifest(
        repo_and_digest=f'{repo}@{sbom.digest}',
        oci_client=oci_client,
    )


def _fetch_blob_from_referrer_manifest(
    repo_and_digest: str,
    oci_client: oc.Client,
) -> bytes:
    '''Fetch a referrer manifest by digest and return its single layer blob.'''
    import json
    manifest_bytes = oci_client.manifest_raw(repo_and_digest).content
    manifest = json.loads(manifest_bytes)
    blob_digest = manifest['layers'][0]['digest']
    repo = om.OciImageReference.to_image_ref(repo_and_digest).ref_without_tag
    return oci_client.blob(
        image_reference=repo,
        digest=blob_digest,
    ).content
