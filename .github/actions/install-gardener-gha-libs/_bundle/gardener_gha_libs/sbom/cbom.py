# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
OCI 1.1 referrer mechanics and OCM resource construction for CBOM documents.

A CBOM is stored as a single-layer OCI referrer manifest, identical in structure to an SBOM
referrer but with a distinct artifactType that includes a `profile=cbom` parameter so
consumers can distinguish CBOM referrers from SBOM referrers via the referrers API.
'''
import logging

import oci.client as oc
import oci.model as om
import ocm

import sbom.oci as _oci

logger = logging.getLogger(__name__)

CBOM_ARTIFACT_TYPE = 'application/vnd.cyclonedx+json;profile=cbom'
# layer media-type is plain CycloneDX JSON (profile is an artifact-level distinction only)
CBOM_LAYER_MEDIA_TYPE = _oci.CYCLONEDX_JSON_MEDIA_TYPE


def push_cbom_referrer(
    cbom_bytes: bytes,
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
    tool_version: str | None = None,
) -> str:
    '''
    Push a CBOM document as an OCI referrer manifest for the given image.

    Returns the digest of the pushed referrer manifest.
    '''
    subject_digest, subject_media_type, subject_size, repo_ref = _oci._resolve_subject(
        image_reference, oci_client,
    )
    return _oci._push_referrer(
        doc_bytes=cbom_bytes,
        doc_media_type=CBOM_LAYER_MEDIA_TYPE,
        artifact_type=CBOM_ARTIFACT_TYPE,
        repo_ref=repo_ref,
        subject_digest=subject_digest,
        subject_media_type=subject_media_type,
        subject_size=subject_size,
        oci_client=oci_client,
        annotations=(
            {'gardener.cloud/cbom/tool-version': tool_version} if tool_version else None
        ),
    )


def build_cbom_ocm_resources(
    resource_name: str,
    version: str,
    source_image_ref: str,
    source_digest: str,
    cbom_bytes: bytes,
    cbom_blob_digest: str,
    tool_ver: str | None,
    cbom_referrer_digest: str | None = None,
) -> tuple[dict, ...]:
    '''
    Build OCM resource dicts for one CBOM document.

    Returns a tuple of one or two dicts:
      - always: a localBlob resource (CBOM inlined into the component descriptor blob store)
      - when cbom_referrer_digest is given: an ociRegistry resource pointing at the referrer

    extraIdentity.cbom-format distinguishes CBOM resources from SBOM resources that share
    the same resource name.
    '''
    extra_identity = {'version': version, 'cbom-format': 'cyclonedx-1.6'}
    label_value = {
        'data-source': {
            'kind': 'local-scan',
            'tool': 'cbomkit-theia',
            'tool-version': tool_ver,
        },
        'format': 'cyclonedx-1.6',
    } if tool_ver else None
    labels = [
        {'name': 'gardener.cloud/cbom/source-image',        'value': source_image_ref},
        {'name': 'gardener.cloud/cbom/source-image-digest', 'value': source_digest},
        *(
            [{'name': 'gardener.cloud/cbom', 'value': label_value}]
            if label_value else []
        ),
    ]

    inline = {
        'name': resource_name,
        'version': version,
        'type': CBOM_LAYER_MEDIA_TYPE,
        'relation': str(ocm.ResourceRelation.EXTERNAL),
        'extraIdentity': extra_identity,
        'access': {
            'type': str(ocm.AccessType.LOCAL_BLOB),
            'localReference': cbom_blob_digest,
            'mediaType': CBOM_LAYER_MEDIA_TYPE,
            'size': len(cbom_bytes),
        },
        'labels': labels,
    }

    if not cbom_referrer_digest:
        return (inline,)

    ref = {
        'name': f'{resource_name}-cbom-ref',
        'version': version,
        'type': CBOM_LAYER_MEDIA_TYPE,
        'relation': str(ocm.ResourceRelation.EXTERNAL),
        'extraIdentity': extra_identity,
        'access': {
            'type': str(ocm.AccessType.OCI_REGISTRY),
            'imageReference': cbom_referrer_digest,
        },
        'labels': labels,
    }
    return (inline, ref)
