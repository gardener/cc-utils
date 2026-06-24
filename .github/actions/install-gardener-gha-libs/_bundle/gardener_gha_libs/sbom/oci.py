# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
OCI 1.1 referrer mechanics for SBOM documents.

An SBOM is stored as a single-layer OCI manifest whose `subject` field points to the image it
describes.  The referrer manifest is pushed by digest; registries that implement the OCI 1.1
referrers API expose it via GET /v2/<repo>/referrers/<digest>.
'''
import hashlib
import json
import logging

import oci.client as oc
import oci.model as om
import ocm

logger = logging.getLogger(__name__)

# empty JSON object — used as the config blob for non-image OCI artefacts
_EMPTY_CONFIG = b'{}'
_EMPTY_CONFIG_DIGEST = f'sha256:{hashlib.sha256(_EMPTY_CONFIG).hexdigest()}'

SPDX_JSON_MEDIA_TYPE = 'application/spdx+json'
CYCLONEDX_JSON_MEDIA_TYPE = 'application/vnd.cyclonedx+json'
OCI_EMPTY_CONFIG_MEDIA_TYPE = 'application/vnd.oci.empty.v1+json'

# (sbom_format_id, media_type) pairs in canonical order
SBOM_FORMATS: tuple[tuple[str, str], ...] = (
    ('spdx-2.3',       SPDX_JSON_MEDIA_TYPE),
    ('cyclonedx-1.6',  CYCLONEDX_JSON_MEDIA_TYPE),
)


def _build_sbom_referrer_manifest(
    sbom_bytes: bytes,
    sbom_media_type: str,
    subject_digest: str,
    subject_media_type: str,
    subject_size: int,
    tool_version: str | None = None,
) -> tuple[bytes, str]:
    '''
    Build an OCI referrer manifest for the given SBOM bytes and subject descriptor.

    Pure function — no I/O.  Returns (manifest_bytes, manifest_digest).

    The caller is responsible for pushing the SBOM blob and the empty config blob
    before pushing the returned manifest.
    '''
    sbom_digest = f'sha256:{hashlib.sha256(sbom_bytes).hexdigest()}'
    sbom_size = len(sbom_bytes)

    manifest = om.OciImageManifest(
        config=om.OciBlobRef(
            digest=_EMPTY_CONFIG_DIGEST,
            mediaType=OCI_EMPTY_CONFIG_MEDIA_TYPE,
            size=len(_EMPTY_CONFIG),
        ),
        layers=[
            om.OciBlobRef(
                digest=sbom_digest,
                mediaType=sbom_media_type,
                size=sbom_size,
            ),
        ],
        subject=om.OciBlobRef(
            digest=subject_digest,
            mediaType=subject_media_type,
            size=subject_size,
        ),
        artifactType=sbom_media_type,
        annotations={
            'org.opencontainers.image.created': _utcnow_iso(),
            **({'gardener.cloud/sbom/tool-version': tool_version} if tool_version else {}),
        },
    )

    manifest_bytes = json.dumps(manifest.as_dict()).encode()
    manifest_digest = f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}'
    return manifest_bytes, manifest_digest


def push_sbom_referrer(
    sbom_path: str,
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
    sbom_media_type: str = SPDX_JSON_MEDIA_TYPE,
    tool_version: str | None = None,
) -> str:
    '''
    Push an SBOM file as an OCI referrer manifest for the given image.

    The SBOM blob is pushed as the single layer of a new OCI manifest; the
    manifest's `subject` is set to the digest-addressed descriptor of the
    target image, establishing the referrer relationship.

    If `tool_version` is given it is recorded in the manifest annotations as
    `gardener.cloud/sbom/tool-version`.

    Returns the digest of the pushed referrer manifest.
    '''
    image_ref = om.OciImageReference.to_image_ref(image_reference)

    # resolve to digest so subject.digest is canonical
    digest_ref = oci_client.to_digest_hash(image_ref)
    digest_image_ref = om.OciImageReference.to_image_ref(digest_ref)
    subject_digest = digest_image_ref.tag  # 'sha256:<hex>'

    manifest_raw_res = oci_client.manifest_raw(digest_image_ref)
    subject_media_type = manifest_raw_res.headers.get(
        'Content-Type', om.OCI_MANIFEST_SCHEMA_V2_MIME,
    )
    subject_size = len(manifest_raw_res.content)

    with open(sbom_path, 'rb') as f:
        sbom_bytes = f.read()

    sbom_digest = f'sha256:{hashlib.sha256(sbom_bytes).hexdigest()}'
    sbom_size = len(sbom_bytes)
    repo_ref = image_ref.ref_without_tag

    logger.info(f'pushing SBOM blob {sbom_digest} ({sbom_size} bytes) to {repo_ref}')
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=sbom_digest,
        octets_count=sbom_size,
        data=sbom_bytes,
        mimetype=sbom_media_type,
    )

    oci_client.put_blob(
        image_reference=repo_ref,
        digest=_EMPTY_CONFIG_DIGEST,
        octets_count=len(_EMPTY_CONFIG),
        data=_EMPTY_CONFIG,
        mimetype=OCI_EMPTY_CONFIG_MEDIA_TYPE,
    )

    manifest_bytes, referrer_digest = _build_sbom_referrer_manifest(
        sbom_bytes=sbom_bytes,
        sbom_media_type=sbom_media_type,
        subject_digest=subject_digest,
        subject_media_type=subject_media_type,
        subject_size=subject_size,
        tool_version=tool_version,
    )

    referrer_ref = f'{repo_ref}@{referrer_digest}'
    logger.info(f'pushing SBOM referrer manifest to {referrer_ref}')
    oci_client.put_manifest(
        image_reference=referrer_ref,
        manifest=manifest_bytes,
    )

    logger.info(f'SBOM referrer pushed: {referrer_digest}')
    return referrer_digest


def push_sbom_referrers(
    spdx_bytes: bytes,
    cdx_bytes: bytes,
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
    tool_version: str | None = None,
) -> tuple[str, str]:
    '''
    Push SPDX and CycloneDX SBOM documents as OCI referrer manifests for the given image.

    Resolves the subject descriptor once and reuses it for both manifests.
    Pushes three blobs (spdx, cdx, empty config) and two manifests.

    Returns (spdx_referrer_digest, cdx_referrer_digest).
    '''
    image_ref = om.OciImageReference.to_image_ref(image_reference)
    repo_ref = image_ref.ref_without_tag

    digest_ref = oci_client.to_digest_hash(image_ref)
    digest_image_ref = om.OciImageReference.to_image_ref(digest_ref)
    subject_digest = digest_image_ref.tag

    manifest_raw_res = oci_client.manifest_raw(digest_image_ref)
    subject_media_type = manifest_raw_res.headers.get(
        'Content-Type', om.OCI_MANIFEST_SCHEMA_V2_MIME,
    )
    subject_size = len(manifest_raw_res.content)

    # push shared empty config once
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=_EMPTY_CONFIG_DIGEST,
        octets_count=len(_EMPTY_CONFIG),
        data=_EMPTY_CONFIG,
        mimetype=OCI_EMPTY_CONFIG_MEDIA_TYPE,
    )

    digests = []
    for sbom_bytes, sbom_media_type in (
        (spdx_bytes, SPDX_JSON_MEDIA_TYPE),
        (cdx_bytes, CYCLONEDX_JSON_MEDIA_TYPE),
    ):
        sbom_digest = f'sha256:{hashlib.sha256(sbom_bytes).hexdigest()}'
        sbom_size = len(sbom_bytes)
        logger.info(f'pushing SBOM blob {sbom_digest} ({sbom_size} bytes) to {repo_ref}')
        oci_client.put_blob(
            image_reference=repo_ref,
            digest=sbom_digest,
            octets_count=sbom_size,
            data=sbom_bytes,
            mimetype=sbom_media_type,
        )
        manifest_bytes, referrer_digest = _build_sbom_referrer_manifest(
            sbom_bytes=sbom_bytes,
            sbom_media_type=sbom_media_type,
            subject_digest=subject_digest,
            subject_media_type=subject_media_type,
            subject_size=subject_size,
            tool_version=tool_version,
        )
        referrer_ref = f'{repo_ref}@{referrer_digest}'
        logger.info(f'pushing SBOM referrer manifest to {referrer_ref}')
        oci_client.put_manifest(
            image_reference=referrer_ref,
            manifest=manifest_bytes,
        )
        digests.append(referrer_digest)

    return digests[0], digests[1]


def build_sbom_ocm_resources(
    resource_name: str,
    version: str,
    source_image_ref: str,
    source_digest: str,
    spdx_bytes: bytes,
    cdx_bytes: bytes,
    spdx_blob_digest: str,
    cdx_blob_digest: str,
    tool_ver: str | None,
) -> tuple[dict, dict]:
    '''
    Build (spdx_resource, cdx_resource) OCM resource dicts for one scanned image.

    Resources use localBlob access (SBOM inlined into the component descriptor blob store).
    Both share the source image's resource name; extraIdentity.sbom-format distinguishes them.
    '''
    def _make(media_type, sbom_format, blob_digest, sbom_bytes):
        label_value = {
            'data-source': {
                'kind': 'local-scan',
                'tool': 'syft',
                'tool-version': tool_ver,
            },
            'format': sbom_format,
        } if tool_ver else None
        return {
            'name': resource_name,
            'version': version,
            'type': media_type,
            'relation': str(ocm.ResourceRelation.EXTERNAL),
            'extraIdentity': {'version': version, 'sbom-format': sbom_format},
            'access': {
                'type': str(ocm.AccessType.LOCAL_BLOB),
                'localReference': blob_digest,
                'mediaType': media_type,
                'size': len(sbom_bytes),
            },
            'labels': [
                {'name': 'gardener.cloud/sbom/source-image',        'value': source_image_ref},
                {'name': 'gardener.cloud/sbom/source-image-digest', 'value': source_digest},
                *(
                    [{'name': 'gardener.cloud/sbom', 'value': label_value}]
                    if label_value else []
                ),
            ],
        }

    return (
        _make(SPDX_JSON_MEDIA_TYPE,      'spdx-2.3',      spdx_blob_digest, spdx_bytes),
        _make(CYCLONEDX_JSON_MEDIA_TYPE, 'cyclonedx-1.6', cdx_blob_digest,  cdx_bytes),
    )


def _utcnow_iso() -> str:
    import datetime
    return datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
