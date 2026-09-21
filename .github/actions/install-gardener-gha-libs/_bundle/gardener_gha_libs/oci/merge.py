import collections.abc
import hashlib
import json

import oci.client as oc
import oci.model as om


def into_image_index(
    src_image_refs: collections.abc.Iterable[str | om.OciImageReference],
    tgt_image_ref: str | om.OciImageReference,
    oci_client: oc.Client,
    extra_tags: collections.abc.Iterable[str]=None,
):
    '''
    merges the given OCI-Artefacts into an OCI Image Index. Source artefacts may either be
    single OCI Image Manifests, or OCI Image Indexes. All artefacts must reside in the same
    OCI Repository.

    If source-artefact is an Image Index, its submanifests will be collected. If if is an OCI Image
    Manifest, it will be added as a single entry into target manifest list.

    As an alternative to OCI Image Index / OCI Image Manifest, it is okay for source images to
    instead be of type Docker Manifest List or Docker Container Image respectively. However,
    all source artefacts must consistently either adhere to Docker or OCI specs (as mixing is not
    allowed, and this function does not offer conversion).

    If inconsistent sources are encountered, ValueError will be raised.

    Passed `extra_tags` will be (re-)set, referring to the same resulting (target) OCI-Image, in
    the same OCI-Repository.
    '''
    target_manifest_index = om.OciImageManifestList(
        manifests=[],
    )
    spec_type = None # will be set to either `docker`, or `oci`

    for src_image_ref in src_image_refs:
        manifest = oci_client.manifest(
            image_reference=src_image_ref,
            accept=om.MimeTypes.prefer_multiarch,
        )

        if manifest.mediaType in (
            om.OCI_IMAGE_INDEX_MIME,
            om.OCI_MANIFEST_SCHEMA_V2_MIME,
        ):
            have_spectype = 'oci'
        elif manifest.mediaType in (
            om.DOCKER_MANIFEST_LIST_MIME,
            om.DOCKER_MANIFEST_SCHEMA_V2_MIME,
        ):
            have_spectype = 'docker'
        else:
            raise ValueError(f'unexpected {manifest.mediaType=}')

        if spec_type is None:
            spec_type = have_spectype
            if spec_type == 'oci':
                target_manifest_index.mediaType = om.OCI_IMAGE_INDEX_MIME
            elif spec_type == 'docker':
                target_manifest_index.mediaType = om.DOCKER_MANIFEST_LIST_MIME
            else:
                raise RuntimeError(f'unexpected {spec_type=} - this is a bug!')
        else:
            if spec_type != have_spectype:
                raise ValueError('cannot mix docker and oci images')

        if isinstance(manifest, om.OciImageManifestList):
            manifest: om.OciImageManifestList

            target_manifest_index.manifests.extend(manifest.manifests)
        elif isinstance(manifest, om.OciImageManifest):
            manifest: om.OciImageManifest

            # we _might_ optimise by always fetch raw-manifest and do custom parsing to save
            # re-downloading here. However, this codepath is expected to be hit rarely, as it has
            # become common to publish image-indexes (even for single-arch, as attestation manifests
            # are enclosed, e.g. from images built w/ docker's docker-build-github-action)
            # -> hence, save the efforts, and accept slight overhead.
            manifest_bytes = oci_client.manifest_raw(
                image_reference=src_image_ref,
                accept=om.MimeTypes.single_image,
            ).content
            manifest_digest = f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}'

            cfg = oci_client.blob(
                image_reference=src_image_ref,
                digest=manifest.config.digest,
                stream=False, # cfg-blob is just a couple hundred of octets
            ).json()

            if spec_type == 'oci':
                submanifest_mimetype = om.OCI_MANIFEST_SCHEMA_V2_MIME
            elif spec_type == 'docker':
                submanifest_mimetype = om.DOCKER_MANIFEST_SCHEMA_V2_MIME
            else:
                raise RuntimeError(f'unexpected {spec_type=} - this is a bug!')

            submanifest_entry = om.OciImageManifestListEntry(
                digest=manifest_digest,
                mediaType=submanifest_mimetype,
                size=len(manifest_bytes),
                platform=om.OciPlatform(
                    architecture=cfg['architecture'],
                    os=cfg['os'],
                )
            )
            target_manifest_index.manifests.append(submanifest_entry)

    target_manifest_bytes = json.dumps(target_manifest_index.as_dict()).encode('utf-8')

    oci_client.put_manifest(
        image_reference=tgt_image_ref,
        manifest=target_manifest_bytes,
    )

    if not extra_tags:
        return

    tgt_ref_without_tag = om.OciImageReference.to_image_ref(tgt_image_ref).ref_without_tag

    for extra_tag in extra_tags:
        extra_tgt_ref = f'{tgt_ref_without_tag}:{extra_tag}'

        oci_client.put_manifest(
            image_reference=extra_tgt_ref,
            manifest=target_manifest_bytes,
        )
