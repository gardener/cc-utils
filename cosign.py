# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum
import hashlib
import json
import logging

import ci.log
import ci.util
import oci.model as om
import oci.client as oc

ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


class OnExist(enum.StrEnum):
    SKIP = 'skip'
    APPEND = 'append'
    OVERWRITE = 'overwrite'


def payload_bytes(
    image_reference: om.OciImageReference | str,
    annotations: dict | None=None,
) -> bytes:
    '''
    returns payload for given OCI Image Reference + optional annotations as output by
    `cosign generate`

    Passed image-reference must have digest-tag.
    '''
    image_reference = om.OciImageReference.to_image_ref(image_reference)
    if not image_reference.has_digest_tag:
        raise ValueError('image-reference must have digest tag', image_reference)

    payload = {
        'critical': {
            'identity': {
                'docker-reference': image_reference.ref_without_tag,
            },
            'image': {
                'docker-manifest-digest': image_reference.tag,
            },
            'type': 'gardener.vnd/oci/cosign-signature',
        },
        'optional': annotations,
    }

    return json.dumps(
        obj=payload,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')


def default_signature_image_reference(
    image_ref: str,
) -> om.OciImageReference:
    '''
    calculate the (default) image reference of the cosign signature for a specific image.

    This image-reference is by default used/expected by cosign if no alternative signature
    repository is specified.
    '''
    parsed_image_ref = om.OciImageReference.to_image_ref(image_ref)
    if not parsed_image_ref.has_digest_tag:
        ValueError('only images that are referenced via digest are allowed')

    parsed_digest = parsed_image_ref.parsed_digest_tag
    alg, val = parsed_digest
    cosign_sig_ref = f'{parsed_image_ref.ref_without_tag}:{alg}-{val}.sig'

    return om.OciImageReference(cosign_sig_ref)


'''
annotation name used to store public-key along cosign signatures within cosign signature artefacts

storing public-key in addition to signature is a preparation for using signature algorithms that
yield different signatures even if signing the same content using same private key (e.g. RSSA-PSS)
'''
_public_key_annotation_name = 'gardener.cloud/cosign-public-key'
_signing_algorithm_annotation_name = 'gardener.cloud/cosign-signing-algorithm'
'''
annotation name used by cosign to store signatures for referenced layer-blob
'''
_cosign_signature_annotation_name = 'dev.cosignproject.cosign/signature'


def sign_image(
    image_reference: om.OciImageReference | str,
    signature: str,
    signing_algorithm: str=None,
    public_key: str=None,
    on_exist: OnExist|str=OnExist.APPEND,
    signature_image_reference: str=None,
    oci_client: oc.Client=None,
):
    '''
    creates an OCI Image signature as understood by cosign. if passed, public-key is added
    as additional annotation `gardener.cloud/cosign-public-key`

    If on_exist is set to `append`, existing signatures (dev.cosignproject/signature annotation)
    will be inspected. If given signature is already present, it will not be added again, thus
    making this function idempotent in this mode.

    In addition, If public-key is passed, existing annotations bearing
    public-key (gardener.cloud/cosign-public-key) will be compared to passed
    public_key and signature algorithm (stored in annotation
    gardener.cloud/cosign-signing-algorithm). If present, given signature will
    not be added, even if it differs from existing one. This behaviour is a
    preparation for different signature methods yielding different signatures
    even if private key and signed payload did not change (such as RSSA-PSS).
    '''
    on_exist = OnExist(on_exist)
    if not signature_image_reference:
        signature_image_reference = default_signature_image_reference(image_reference)

    if not oci_client:
        import ccc.oci
        oci_client = ccc.oci.oci_client()

    image_reference = om.OciImageReference.to_image_ref(image_reference)
    if not image_reference.has_tag:
        raise ValueError(image_reference, 'tag is required')
    if not image_reference.has_digest_tag:
        digest = hashlib.sha256(
            oci_client.manifest_raw(image_reference).content,
        ).hexdigest()
        image_reference = f'{image_reference.ref_without_tag}@sha256{digest}'

    if on_exist in (OnExist.SKIP, OnExist.APPEND):
        exists = bool(oci_client.head_manifest(
            image_reference=signature_image_reference,
            absent_ok=True,
        ))

    if on_exist is OnExist.SKIP and exists:
        logger.info(f'signature artefact exists: {signature_image_reference} - skipping')
        return

    # payload is normalised JSON w/ reference to signed image. It is expected as (only)
    # layer-blob for signature artefact
    payload = payload_bytes(
        image_reference=image_reference,
    )
    payload_size = len(payload)
    payload_digest = f'sha256:{hashlib.sha256(payload).hexdigest()}'

    if on_exist is OnExist.APPEND and exists:
        manifest = oci_client.manifest(
            image_reference=signature_image_reference,
        )
        for layer_idx, layer in enumerate(manifest.layers):
            a = layer.annotations
            existing_signature = a.get(_cosign_signature_annotation_name, None)
            # todo: ideally, signatures should be parsed and compared independent of format
            #       cosign seems to expect base64-part w/o PEM-headers (BEGIN/END), though
            # todo2: should we also check digest (there might be multiple payloads in same
            #        signature artefact; otoh, collissions are unlikely, so it should be safe to
            #        not check this explicitly
            if existing_signature == signature:
                logger.info(f'found signature in {layer_idx=} for {signature_image_reference=}')
                logger.info('skipping (will not redundantly add signature again)')
                return

            existing_public_key = a.get(_public_key_annotation_name, None)
            if not existing_public_key or not public_key:
                continue

            existing_signing_algorithm = a.get(_signing_algorithm_annotation_name, None)
            if signing_algorithm and existing_signing_algorithm and \
                signing_algorithm != existing_signing_algorithm:
                # we found an existing signature, however with different signing algorithm
                # -> do not skip, as resigning with different algorithm is likely to be
                #    caller's intent
                continue

            if existing_public_key == public_key:
                logger.info(
                    f'found matching public key in {layer_idx=} for {signature_image_reference=}'
                )
                logger.info('skipping (will not redundantly add signature again)')
                return

        # if this line is reached, we did not find the signature we are about to append

        for layer in manifest.layers:
            if layer.digest == payload_digest:
                upload_payload = False
                break
        else:
            # payload not yet present
            upload_payload = True
    else:
        upload_payload = True
        manifest = None

    if upload_payload:
        oci_client.put_blob(
            image_reference=signature_image_reference,
            digest=payload_digest,
            octets_count=payload_size,
            data=payload,
        )

        # dummy cfg-blob as generated by cosign
        cfg_blob = json.dumps({
            'architecture': '',
            'config': {},
            'created': '0001-01-01T00:00:00Z',
            'history': [{'created': '0001-01-01T00:00:00Z'}],
            'os': '',
            'rootfs': {
                'diff_ids': [payload_digest],
                'type': 'layers',
            },
        },
            separators=(',', ':'),
            sort_keys=True,
        ).encode('utf-8')
        cfg_blob_size = len(cfg_blob)
        cfg_blob_digest = f'sha256:{hashlib.sha256(cfg_blob).hexdigest()}'

        oci_client.put_blob(
            image_reference=image_reference,
            digest=cfg_blob_digest,
            octets_count=cfg_blob_size,
            data=cfg_blob,
        )

    signature_layer = om.OciBlobRef(
        digest=payload_digest,
        size=payload_size,
        mediaType='application/vnd.dev.cosign.simplesigning.v1+json',
        annotations={
            _cosign_signature_annotation_name: signature,
        },
    )
    if public_key:
        signature_layer.annotations[_public_key_annotation_name] = public_key
    if signing_algorithm:
        signature_layer.annotations[_signing_algorithm_annotation_name] = signing_algorithm

    if not manifest:
        manifest = om.OciImageManifest(
            config=om.OciBlobRef(
                digest=cfg_blob_digest,
                mediaType='application/vnd.oci.image.config.v1+json',
                size=cfg_blob_size,
            ),
            mediaType='application/vnd.oci.image.manifest.v1+json',
            layers=[
                signature_layer,
            ],
            annotations={},
    )
    else:
        manifest.layers.append(signature_layer)

    manifest_bytes = json.dumps(manifest.as_dict()).encode('utf-8')

    oci_client.put_manifest(
        image_reference=signature_image_reference,
        manifest=manifest_bytes,
    )
