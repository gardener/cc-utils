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


def _build_referrer_manifest(
    doc_bytes: bytes,
    doc_media_type: str,
    artifact_type: str,
    subject_digest: str,
    subject_media_type: str,
    subject_size: int,
    annotations: dict | None = None,
) -> tuple[bytes, str]:
    '''
    Build an OCI referrer manifest for a single-layer document blob.

    Pure function — no I/O.  Returns (manifest_bytes, manifest_digest).

    The caller is responsible for pushing the document blob and the empty config blob
    before pushing the returned manifest.
    '''
    doc_digest = f'sha256:{hashlib.sha256(doc_bytes).hexdigest()}'

    manifest = om.OciImageManifest(
        config=om.OciBlobRef(
            digest=_EMPTY_CONFIG_DIGEST,
            mediaType=OCI_EMPTY_CONFIG_MEDIA_TYPE,
            size=len(_EMPTY_CONFIG),
        ),
        layers=[
            om.OciBlobRef(
                digest=doc_digest,
                mediaType=doc_media_type,
                size=len(doc_bytes),
            ),
        ],
        subject=om.OciBlobRef(
            digest=subject_digest,
            mediaType=subject_media_type,
            size=subject_size,
        ),
        artifactType=artifact_type,
        annotations={
            'org.opencontainers.image.created': _utcnow_iso(),
            **(annotations or {}),
        },
    )

    manifest_bytes = json.dumps(manifest.as_dict()).encode()
    manifest_digest = f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}'
    return manifest_bytes, manifest_digest


# kept for backwards-compat — delegates to the generic helper
def _build_sbom_referrer_manifest(
    sbom_bytes: bytes,
    sbom_media_type: str,
    subject_digest: str,
    subject_media_type: str,
    subject_size: int,
    tool_version: str | None = None,
) -> tuple[bytes, str]:
    return _build_referrer_manifest(
        doc_bytes=sbom_bytes,
        doc_media_type=sbom_media_type,
        artifact_type=sbom_media_type,
        subject_digest=subject_digest,
        subject_media_type=subject_media_type,
        subject_size=subject_size,
        annotations=(
            {'gardener.cloud/sbom/tool-version': tool_version} if tool_version else None
        ),
    )


def _resolve_subject(
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
) -> tuple[str, str, int, str]:
    '''
    Resolve an image reference to its subject descriptor fields plus the repo ref.

    Returns (subject_digest, subject_media_type, subject_size, repo_ref).
    '''
    image_ref = om.OciImageReference.to_image_ref(image_reference)
    digest_ref = oci_client.to_digest_hash(image_ref)
    digest_image_ref = om.OciImageReference.to_image_ref(digest_ref)
    subject_digest = digest_image_ref.tag  # 'sha256:<hex>'
    manifest_raw_res = oci_client.manifest_raw(digest_image_ref)
    subject_media_type = manifest_raw_res.headers.get(
        'Content-Type', om.OCI_MANIFEST_SCHEMA_V2_MIME,
    )
    subject_size = len(manifest_raw_res.content)
    return subject_digest, subject_media_type, subject_size, image_ref.ref_without_tag


def _push_referrer(
    doc_bytes: bytes,
    doc_media_type: str,
    artifact_type: str,
    repo_ref: str,
    subject_digest: str,
    subject_media_type: str,
    subject_size: int,
    oci_client: oc.Client,
    annotations: dict | None = None,
) -> str:
    '''
    Push a document blob as an OCI referrer manifest. Returns the referrer digest.
    '''
    doc_digest = f'sha256:{hashlib.sha256(doc_bytes).hexdigest()}'
    logger.info(f'pushing doc blob {doc_digest} ({len(doc_bytes)} bytes) to {repo_ref}')
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=doc_digest,
        octets_count=len(doc_bytes),
        data=doc_bytes,
        mimetype=doc_media_type,
    )
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=_EMPTY_CONFIG_DIGEST,
        octets_count=len(_EMPTY_CONFIG),
        data=_EMPTY_CONFIG,
        mimetype=OCI_EMPTY_CONFIG_MEDIA_TYPE,
    )
    manifest_bytes, referrer_digest = _build_referrer_manifest(
        doc_bytes=doc_bytes,
        doc_media_type=doc_media_type,
        artifact_type=artifact_type,
        subject_digest=subject_digest,
        subject_media_type=subject_media_type,
        subject_size=subject_size,
        annotations=annotations,
    )
    referrer_ref = f'{repo_ref}@{referrer_digest}'
    logger.info(f'pushing referrer manifest to {referrer_ref}')
    oci_client.put_manifest(image_reference=referrer_ref, manifest=manifest_bytes)
    return referrer_digest


def push_sbom_referrer(
    sbom_path: str,
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
    sbom_media_type: str = SPDX_JSON_MEDIA_TYPE,
    tool_version: str | None = None,
) -> str:
    '''
    Push an SBOM file as an OCI referrer manifest for the given image.

    Returns the digest of the pushed referrer manifest.
    '''
    with open(sbom_path, 'rb') as f:
        sbom_bytes = f.read()

    subject_digest, subject_media_type, subject_size, repo_ref = _resolve_subject(
        image_reference, oci_client,
    )
    return _push_referrer(
        doc_bytes=sbom_bytes,
        doc_media_type=sbom_media_type,
        artifact_type=sbom_media_type,
        repo_ref=repo_ref,
        subject_digest=subject_digest,
        subject_media_type=subject_media_type,
        subject_size=subject_size,
        oci_client=oci_client,
        annotations=(
            {'gardener.cloud/sbom/tool-version': tool_version} if tool_version else None
        ),
    )


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

    Returns (spdx_referrer_digest, cdx_referrer_digest).
    '''
    subject_digest, subject_media_type, subject_size, repo_ref = _resolve_subject(
        image_reference, oci_client,
    )
    annotations = {'gardener.cloud/sbom/tool-version': tool_version} if tool_version else None
    digests = []
    for sbom_bytes, sbom_media_type in (
        (spdx_bytes, SPDX_JSON_MEDIA_TYPE),
        (cdx_bytes, CYCLONEDX_JSON_MEDIA_TYPE),
    ):
        digests.append(_push_referrer(
            doc_bytes=sbom_bytes,
            doc_media_type=sbom_media_type,
            artifact_type=sbom_media_type,
            repo_ref=repo_ref,
            subject_digest=subject_digest,
            subject_media_type=subject_media_type,
            subject_size=subject_size,
            oci_client=oci_client,
            annotations=annotations,
        ))
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


def push_sbom_standalone(
    spdx_bytes: bytes,
    cdx_bytes: bytes,
    repo_ref: str,
    content_digest: str,
    oci_client: oc.Client,
    tool_version: str | None = None,
) -> tuple[str, str]:
    '''
    Push SPDX and CycloneDX SBOM documents as standalone OCI manifests (no `subject` field).

    Used for S3-backed resources that have no OCI image to hang referrers off.  Each document
    is pushed as a single-layer manifest; the tag used is derived from `content_digest` so the
    result is content-addressable and cache lookups are cheap (HEAD request).

    Returns (spdx_manifest_digest, cdx_manifest_digest).
    '''
    annotations = {'gardener.cloud/sbom/tool-version': tool_version} if tool_version else None
    digests = []
    for doc_bytes, doc_media_type, artifact_type in (
        (spdx_bytes, SPDX_JSON_MEDIA_TYPE,      SPDX_JSON_MEDIA_TYPE),
        (cdx_bytes,  CYCLONEDX_JSON_MEDIA_TYPE,  CYCLONEDX_JSON_MEDIA_TYPE),
    ):
        doc_digest = f'sha256:{hashlib.sha256(doc_bytes).hexdigest()}'
        logger.info(f'pushing standalone doc blob {doc_digest} to {repo_ref}')
        oci_client.put_blob(
            image_reference=repo_ref,
            digest=doc_digest,
            octets_count=len(doc_bytes),
            data=doc_bytes,
            mimetype=doc_media_type,
        )
        oci_client.put_blob(
            image_reference=repo_ref,
            digest=_EMPTY_CONFIG_DIGEST,
            octets_count=len(_EMPTY_CONFIG),
            data=_EMPTY_CONFIG,
            mimetype=OCI_EMPTY_CONFIG_MEDIA_TYPE,
        )
        manifest = om.OciImageManifest(
            config=om.OciBlobRef(
                digest=_EMPTY_CONFIG_DIGEST,
                mediaType=OCI_EMPTY_CONFIG_MEDIA_TYPE,
                size=len(_EMPTY_CONFIG),
            ),
            layers=[
                om.OciBlobRef(
                    digest=doc_digest,
                    mediaType=doc_media_type,
                    size=len(doc_bytes),
                ),
            ],
            artifactType=artifact_type,
            annotations={
                'org.opencontainers.image.created': _utcnow_iso(),
                **(annotations or {}),
            },
        )
        manifest_bytes = json.dumps(manifest.as_dict()).encode()
        manifest_digest = f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}'
        logger.info(f'pushing standalone SBOM manifest to {repo_ref}@{manifest_digest}')
        oci_client.put_manifest(
            image_reference=f'{repo_ref}@{manifest_digest}',
            manifest=manifest_bytes,
        )
        digests.append(manifest_digest)
    return digests[0], digests[1]


def lookup_sbom_standalone(
    repo_ref: str,
    spdx_manifest_digest: str,
    cdx_manifest_digest: str,
    oci_client: oc.Client,
) -> tuple[bytes, bytes] | None:
    '''
    Check whether standalone SBOM manifests already exist in the registry.

    Returns (spdx_bytes, cdx_bytes) if both are present, otherwise None.
    The digests come from the synthetic OCI ref built from the S3 content hash.
    '''
    try:
        spdx_manifest_bytes = oci_client.manifest_raw(
            f'{repo_ref}@{spdx_manifest_digest}',
            absent_ok=True,
        )
        cdx_manifest_bytes = oci_client.manifest_raw(
            f'{repo_ref}@{cdx_manifest_digest}',
            absent_ok=True,
        )
    except Exception:  # nosec B110
        return None

    if spdx_manifest_bytes is None or cdx_manifest_bytes is None:
        return None

    def _get_layer_blob(manifest_bytes_resp, repo) -> bytes:
        m = json.loads(manifest_bytes_resp.content)
        blob_digest = m['layers'][0]['digest']
        return oci_client.blob(image_reference=repo, digest=blob_digest).content

    try:
        spdx_bytes = _get_layer_blob(spdx_manifest_bytes, repo_ref)
        cdx_bytes = _get_layer_blob(cdx_manifest_bytes, repo_ref)
        return spdx_bytes, cdx_bytes
    except Exception as e:
        logger.warning(f'failed to fetch existing standalone SBOM blobs from {repo_ref}: {e}')
        return None


def _utcnow_iso() -> str:
    import datetime
    return datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
