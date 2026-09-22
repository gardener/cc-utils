'''
collection of workarounds required to deal w/ different OCI Registries' idiosyncrazies
'''

import dataclasses
import hashlib
import json
import logging

import requests

import oci.client as oc
import oci.model as om


logger = logging.getLogger(__name__)


def _cfg_blob_non_empty_history_layers(cfg_blob: dict) -> list[dict]:
    history = cfg_blob['history']

    non_empty = [
        entry for entry in history if not entry.get('empty_layer', False)
    ]

    return non_empty


def is_cfg_blob_sane(
    manifest: om.OciImageManifest,
    cfg_blob: bytes | dict,
) -> bool:
    if isinstance(cfg_blob, (bytes, str)):
        cfg_blob = json.loads(cfg_blob)
    if not isinstance(cfg_blob, dict):
        raise ValueError(cfg_blob)

    non_empty_layers_in_cfg_blob = len(_cfg_blob_non_empty_history_layers(cfg_blob=cfg_blob))

    if non_empty_layers_in_cfg_blob < len(manifest.layers):
        return False

    return True


def sanitise_cfg_blob(
    manifest: om.OciImageManifest,
    cfg_blob: bytes | dict,
) -> bytes | dict:
    '''
    returns a sanitised form of the passed cfg-blob. If the passed cfg-blob was already considered
    "sane", the returned object is identical to the passed-in cfg_blob argument.

    otherwise, a sanitised cfg-blob is returned as a utf-8-encoded `bytes` object.
    '''
    if is_cfg_blob_sane(manifest=manifest, cfg_blob=cfg_blob):
        return cfg_blob

    if isinstance(cfg_blob, (bytes, str)):
        cfg_blob = json.loads(cfg_blob)

    cfg_blob_nonempty_layers = _cfg_blob_non_empty_history_layers(cfg_blob=cfg_blob)
    missing_history_entries = len(manifest.layers) - len(cfg_blob_nonempty_layers)

    if not cfg_blob_nonempty_layers:
        raise ValueError('cannot duplicate fake history-entries w/o at least one non-empty entry')

    # arbitrarily choose first entry to duplicate
    history_entry = cfg_blob_nonempty_layers[0]

    for _ in range(missing_history_entries):
        cfg_blob['history'].append(history_entry)

    return json.dumps(cfg_blob).encode('utf-8')


def sanitise_image(
    image_ref: str | om.OciImageReference,
    oci_client: oc.Client,
):
    manifest = oci_client.manifest(image_reference=image_ref)
    cfg_blob = oci_client.blob(image_reference=image_ref, digest=manifest.config.digest).content

    if is_cfg_blob_sane(manifest=manifest, cfg_blob=cfg_blob):
        return image_ref

    sanitised_cfg_blob = sanitise_cfg_blob(manifest=manifest, cfg_blob=cfg_blob)
    cfg_blob_digest = 'sha256:' + hashlib.sha256(sanitised_cfg_blob).hexdigest()

    oci_client.put_blob(
        image_ref,
        digest=cfg_blob_digest,
        octets_count=len(sanitised_cfg_blob),
        data=sanitised_cfg_blob,
    )

    manifest = dataclasses.replace(
        manifest,
        config=dataclasses.replace(
            manifest.config,
            digest=cfg_blob_digest,
            size=len(sanitised_cfg_blob),
        ),
    )

    manifest_bytes = json.dumps(manifest.as_dict()).encode('utf-8')

    oci_client.put_manifest(image_reference=image_ref, manifest=manifest_bytes)

    manifest_dig = 'sha256:' + hashlib.sha256(manifest_bytes).hexdigest()
    img_ref: om.OciImageReference = om.OciImageReference.to_image_ref(image_ref)

    patched_img_ref = f'{img_ref.ref_without_tag}@{manifest_dig}'

    return patched_img_ref


def patch_head_blob_to_use_get(oci_client: oc.Client) -> None:
    '''
    it has been observed that some versions of Artifactory will, for the same (and actually
    absent) blob, inconsistently yield HTTP 200 for a HEAD request and HTTP 404 for a GET
    request. This workaround patches `oci_client.head_blob` to actually issue a GET (response
    body discarded, unread), so existence-checks are no longer fooled.
    '''
    def _head_blob_via_get(image_reference, digest, absent_ok=True):
        try:
            response = oci_client.blob(
                image_reference=image_reference,
                digest=digest,
                stream=True,
                absent_ok=False,
                warn_if_not_ok=not absent_ok,
            )
        except requests.exceptions.HTTPError as e:
            response = e.response
            response.close()
            if absent_ok and response.status_code == 404:
                return response
            raise
        response.close() # release connection without reading body
        return response

    oci_client.head_blob = _head_blob_via_get


def patch_head_blob_to_ignore_existing_blobs(oci_client: oc.Client) -> None:
    '''
    it has been observed that Artifactory will, for a blob that HEAD/GET both correctly report
    as present, still reject a manifest-PUT referencing it with "MANIFEST_INVALID" / "failed to
    copy blob to <path>". Unlike `patch_head_blob_to_use_get` (which addresses the opposite
    HEAD/GET inconsistency), this workaround makes `oci_client.head_blob` unconditionally report
    "absent", forcing every blob to be re-uploaded on each replication, which has been observed to
    work around the copy-failure. This trades away the "skip already-present blobs" optimisation
    entirely, so only enable it where that cost is acceptable; never combine with
    `patch_head_blob_to_use_get`.
    '''
    def _head_blob_always_absent(image_reference, digest, absent_ok=True):
        response = requests.models.Response()
        response.status_code = 404
        response.reason = 'Not Found (forced-absent by patch_head_blob_to_ignore_existing_blobs)'
        return response

    oci_client.head_blob = _head_blob_always_absent
    logger.info('patched head_blob to unconditionally report blobs as absent (force-reupload)')


def patch_put_manifest_to_validate_via_get(oci_client: oc.Client) -> None:
    '''
    it has been observed that Artifactory will (in certain situations) accept a manifest-PUT
    (yielding HTTP 20x) while silently dropping the manifest, such that subsequent GETs will
    yield HTTP 404. This workaround patches `oci_client.put_manifest` to - after each successful
    PUT - issue a GET to validate the manifest is actually being served from the target registry.
    Replication will thus fail early, rather than erroneously being marked as done.
    '''
    orig_put_manifest = oci_client.put_manifest

    def _put_manifest_validating(image_reference, manifest: bytes, *args, **kwargs):
        res = orig_put_manifest(
            image_reference,
            manifest,
            *args,
            **kwargs,
        )

        validation_ref = image_reference
        if not om.OciImageReference.to_image_ref(image_reference).has_digest_tag:
            manifest_digest = 'sha256:' + hashlib.sha256(manifest).hexdigest()
            validation_ref = (
                f'{om.OciImageReference.to_image_ref(image_reference).ref_without_tag}'
                f'@{manifest_digest}'
            )

        try:
            oci_client.manifest_raw(
                image_reference=validation_ref,
                accept=om.MimeTypes.multiarch,
                absent_ok=False,
            ).close()
        except requests.exceptions.HTTPError as he:
            he.add_note(
                f'manifest was accepted (HTTP 20x) but is not served from replication-target '
                f'{image_reference=} (validated via {validation_ref=})'
            )
            raise

        return res

    oci_client.put_manifest = _put_manifest_validating
    logger.info('patched put_manifest to validate each upload via GET (paranoid mode)')
