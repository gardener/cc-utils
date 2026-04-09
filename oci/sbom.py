'''
Utilities for pushing SBOM documents as OCI referrers (OCI 1.1).

An SBOM is stored as a single-layer OCI manifest whose `subject` field points
to the image it describes.  The referrer manifest is pushed by digest; registries
that implement the OCI 1.1 referrers API expose it via GET /v2/<repo>/referrers/<digest>.
'''
import hashlib
import json
import logging

import oci.client as oc
import oci.model as om

logger = logging.getLogger(__name__)

# empty JSON object — used as the config blob for non-image OCI artefacts
_EMPTY_CONFIG = b'{}'
_EMPTY_CONFIG_DIGEST = f'sha256:{hashlib.sha256(_EMPTY_CONFIG).hexdigest()}'

SPDX_JSON_MEDIA_TYPE = 'application/spdx+json'
OCI_EMPTY_CONFIG_MEDIA_TYPE = 'application/vnd.oci.empty.v1+json'


def push_sbom_referrer(
    sbom_path: str,
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
    sbom_media_type: str = SPDX_JSON_MEDIA_TYPE,
) -> str:
    '''
    Push an SBOM file as an OCI referrer manifest for the given image.

    The SBOM blob is pushed as the single layer of a new OCI manifest; the
    manifest's `subject` is set to the digest-addressed descriptor of the
    target image, establishing the referrer relationship.

    Returns the digest of the pushed referrer manifest.
    '''
    image_ref = om.OciImageReference.to_image_ref(image_reference)

    # resolve to digest so subject.digest is canonical
    digest_ref = oci_client.to_digest_hash(image_ref)
    # to_digest_hash returns a full ref like repo@sha256:...
    digest_image_ref = om.OciImageReference.to_image_ref(digest_ref)
    subject_digest = digest_image_ref.tag  # 'sha256:<hex>'

    # retrieve the manifest to get its size and mediaType for the subject descriptor
    manifest_raw_res = oci_client.manifest_raw(digest_image_ref)
    manifest_bytes_for_subject = manifest_raw_res.content
    subject_media_type = manifest_raw_res.headers.get('Content-Type', om.OCI_MANIFEST_SCHEMA_V2_MIME)
    subject_size = len(manifest_bytes_for_subject)

    with open(sbom_path, 'rb') as f:
        sbom_bytes = f.read()

    sbom_digest = f'sha256:{hashlib.sha256(sbom_bytes).hexdigest()}'
    sbom_size = len(sbom_bytes)
    repo_ref = image_ref.ref_without_tag  # push blobs/manifest to same repo

    logger.info(f'pushing SBOM blob {sbom_digest} ({sbom_size} bytes) to {repo_ref}')
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=sbom_digest,
        octets_count=sbom_size,
        data=sbom_bytes,
        mimetype=sbom_media_type,
    )

    # empty config blob (required by OCI image manifest structure)
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=_EMPTY_CONFIG_DIGEST,
        octets_count=len(_EMPTY_CONFIG),
        data=_EMPTY_CONFIG,
        mimetype=OCI_EMPTY_CONFIG_MEDIA_TYPE,
    )

    referrer_manifest = om.OciImageManifest(
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
        },
    )

    referrer_manifest_bytes = json.dumps(referrer_manifest.as_dict()).encode()
    referrer_digest = f'sha256:{hashlib.sha256(referrer_manifest_bytes).hexdigest()}'

    # push manifest addressed by digest (no symbolic tag needed for referrers API)
    referrer_ref = f'{repo_ref}@{referrer_digest}'
    logger.info(f'pushing SBOM referrer manifest to {referrer_ref}')
    oci_client.put_manifest(
        image_reference=referrer_ref,
        manifest=referrer_manifest_bytes,
    )

    logger.info(f'SBOM referrer pushed: {referrer_digest}')
    return referrer_digest


def _utcnow_iso() -> str:
    import datetime
    return datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
