import dataclasses
import hashlib
import json
import logging
import tarfile
import typing
import zlib

import dacite
import requests

import oci.auth as oa
import oci.client as oc
import oci.convert as oconv
import oci.kaniko as ok
import oci.model as om
import oci.util as ou

logger = logging.getLogger(__name__)

# type-alias for typehints
image_reference = str


def replicate_artifact(
    src_image_reference: str,
    tgt_image_reference: str,
    credentials_lookup: oa.credentials_lookup=None,
    routes: oc.OciRoutes=oc.OciRoutes(),
    oci_client: oc.Client=None,
) -> requests.Response:
    '''
    verbatimly replicate the OCI Artifact from src -> tgt without taking any assumptions
    about the transported contents. This in particular allows contents to be replicated
    that are not e.g. "docker-compliant" OCI Images.
    '''
    if not (bool(credentials_lookup) ^ bool(oci_client)):
        raise ValueError('either credentials-lookup + routes, xor client must be passed')

    src_image_reference = ou.normalise_image_reference(src_image_reference)
    tgt_image_reference = ou.normalise_image_reference(tgt_image_reference)

    if not oci_client:
        client = oc.Client(
            credentials_lookup=credentials_lookup,
            routes=routes,
        )
    else:
        client = oci_client

    # we need the unaltered - manifest for verbatim replication
    raw_manifest = client.manifest_raw(
        image_reference=src_image_reference,
    ).text
    manifest = json.loads(raw_manifest)
    schema_version = int(manifest['schemaVersion'])
    if schema_version == 1:
        manifest = client.manifest(image_reference=src_image_reference)

        logger.warning(
          f'''
          manifest {src_image_reference=} is in legacy-format
          (schemaVersion==1). Cannot verbatimly replicate
          '''
        )

        manifest, _ = oconv.v1_manifest_to_v2(
            manifest=manifest,
            oci_client=client,
            tgt_image_ref=tgt_image_reference,
        )

        # we must determine the uncompressed layer-digests to synthesise a valid
        # cfg-blob docker will accept (this means in particular we must download
        # all layers, even if we do not need to upload them)
        need_uncompressed_layer_digests = True
        uncompressed_layer_digests = []
    elif schema_version == 2:
        manifest = dacite.from_dict(
            data_class=om.OciImageManifest,
            data=json.loads(raw_manifest)
        )
        need_uncompressed_layer_digests = False
        uncompressed_layer_digests = None
    else:
      raise NotImplementedError(schema_version)

    need_to_synthesise_cfg_blob = False

    for idx, layer in enumerate(manifest.blobs()):
        # need to specially handle manifest (may be absent for v2 / legacy images)
        is_manifest = idx == 0

        head_res = client.head_blob(
            image_reference=tgt_image_reference,
            digest=layer.digest,
        )
        if head_res.ok:
            if not need_uncompressed_layer_digests:
                logger.info(f'skipping blob download {layer.digest=} - already exists in tgt')
                continue # no need to download if blob already exists in tgt
            elif not is_manifest:
                # we will not need to re-upload, however we do need the uncompressed digest
                blob_res = client.blob(
                    image_reference=src_image_reference,
                    digest=layer.digest,
                    absent_ok=is_manifest,
                )

                layer_hash = hashlib.sha256()
                decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)

                for chunk in blob_res.iter_content(chunk_size=4096):
                    layer_hash.update(decompressor.decompress(chunk))

                uncompressed_layer_digests.append(f'sha256:{layer_hash.hexdigest()}')
                continue # we may still skip the upload, of course

        blob_res = client.blob(
            image_reference=src_image_reference,
            digest=layer.digest,
            absent_ok=is_manifest,
        )
        if not blob_res and is_manifest:
            # fallback to non-verbatim replication; synthesise cfg
            logger.warning(
                'falling back to non-verbatim replication '
                '{src_image_reference=} {tgt_image_reference=}'
            )
            need_to_synthesise_cfg_blob = True
            continue

        if need_uncompressed_layer_digests:
            uncompressed_layer_hash = hashlib.sha256()
            decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)

            def intercept_chunks():
                for chunk in blob_res.iter_content(chunk_size=4096):
                    uncompressed_layer_hash.update(decompressor.decompress(chunk))
                    yield chunk

                uncompressed_layer_digests.append(f'sha256:{uncompressed_layer_hash.hexdigest()}')

            blob_res = intercept_chunks()

        client.put_blob(
            image_reference=tgt_image_reference,
            digest=layer.digest,
            octets_count=layer.size,
            data=blob_res,
        )

    if need_to_synthesise_cfg_blob:
        fake_cfg_dict = json.loads(json.loads(raw_manifest)['history'][0]['v1Compatibility'])

        # patch-in uncompressed layer-digests
        fake_cfg_dict['rootfs'] = {
            'diff_ids': uncompressed_layer_digests,
            'type': 'layers',
        }

        fake_cfg_raw = json.dumps(fake_cfg_dict).encode('utf-8')

        client.put_blob(
            image_reference=tgt_image_reference,
            digest=(cfg_digest := f'sha256:{hashlib.sha256(fake_cfg_raw).hexdigest()}'),
            octets_count=len(fake_cfg_raw),
            data=fake_cfg_raw,
        )

        manifest_dict = dataclasses.asdict(manifest)
        # patch-on altered cfg-digest
        manifest_dict['config']['digest'] = cfg_digest
        manifest_dict['config']['size'] = len(fake_cfg_raw)
        raw_manifest = json.dumps(manifest_dict)

    return client.put_manifest(
        image_reference=tgt_image_reference,
        manifest=raw_manifest,
    )


def replicate_blobs(
    src_ref: str,
    src_oci_manifest: om.OciImageManifest,
    tgt_ref: str,
    oci_client: oc.Client,
    blob_overwrites: typing.Dict[om.OciBlobRef, typing.Union[bytes, typing.BinaryIO]],
) -> om.OciImageManifest:
    '''
    replicates blobs from given oci-image-ref to the specified target-ref, optionally replacing
    the specified blobs. This is particularly useful for replacing some "special" blobs, such
    as a component-descriptor layer blob or the config-blob.

    Note that the uploaded artifact must be finalised after the upload by a "manifest-put".
    '''
    blob_overwrites = {k.digest:v for k,v in blob_overwrites.items()}

    def replicate_blob(blob: om.OciBlobRef) -> om.OciBlobRef:
        if blob_overwrite_bytes := blob_overwrites.get(blob.digest):
            logger.info(f'Replicate with overwriting {blob=}')

            if hasattr(blob_overwrite_bytes, 'read'):
                digest = hashlib.sha256()
                blob_overwrite_bytes.seek(0)
                while (chunk := blob_overwrite_bytes.read(4096)):
                    digest.update(chunk)
                digest = f'sha256:{digest.hexdigest()}'
                octets_count = blob_overwrite_bytes.tell()
                blob_overwrite_bytes.seek(0)
            else:
                digest = f'sha256:{hashlib.sha256(blob_overwrite_bytes).hexdigest()}'
                octets_count = len(blob_overwrite_bytes)

            oci_client.put_blob(
                image_reference=tgt_ref,
                digest=digest,
                octets_count=octets_count,
                data=blob_overwrite_bytes,
            )
            return om.OciBlobRef(
                digest=digest,
                mediaType=blob.mediaType, #XXX: pass-in new media type?
                size=octets_count,
            )
        else:
            digest = blob.digest

            src_blob: requests.models.Response = oci_client.blob(
                image_reference=src_ref,
                digest=digest,
            )

            octets_count = int(src_blob.headers['Content-Length'])

            oci_client.put_blob(
                image_reference=tgt_ref,
                digest=digest,
                octets_count=octets_count,
                data=src_blob,
            )
            return om.OciBlobRef(
                digest=digest,
                mediaType=blob.mediaType,
                size=octets_count,
            )

    return om.OciImageManifest(
        config=replicate_blob(src_oci_manifest.config),
        layers=[replicate_blob(blob) for blob in src_oci_manifest.layers],
    )


def publish_container_image_from_kaniko_tarfile(
    image_tarfile_path: str,
    oci_client: oc.Client,
    image_reference: str,
    additional_tags: typing.List[str]=(),
    manifest_mimetype: str=om.OCI_MANIFEST_SCHEMA_V2_MIME,
):
    image_reference = ou.normalise_image_reference(image_reference=image_reference)
    image_name = image_reference.rsplit(':', 1)[0]
    image_references = (image_reference,) + tuple([f'{image_name}:{tag}' for tag in additional_tags])

    with ok.read_kaniko_image_tar(tar_path=image_tarfile_path) as image:
        chunk_size = 1024 * 1024
        for kaniko_blob in image.blobs():
            oci_client.put_blob(
                image_reference=image_reference,
                digest=kaniko_blob.digest_str(),
                octets_count=kaniko_blob.size,
                data=kaniko_blob,
                max_chunk=chunk_size,
            )

        # optionally patch manifest's mimetype (e.g. required for docker-hub)
        manifest_dict = dataclasses.asdict(image.oci_manifest())
        manifest_dict['mediaType'] = manifest_mimetype

        manifest_bytes = json.dumps(
            manifest_dict,
        ).encode('utf-8')

        for tgt_ref in image_references:
            logger.info(f'publishing manifest {tgt_ref=}')
            oci_client.put_manifest(
                image_reference=tgt_ref,
                manifest=manifest_bytes,
            )


def image_layers_as_tarfile_generator(
    image_reference: str,
    oci_client: oc.Client,
    chunk_size=tarfile.RECORDSIZE,
    include_config_blob=True,
) -> typing.Generator[bytes, None, None]:
    '''
    returns a generator yielding a tar-archive with the passed oci-image's layer-blobs as
    members. This is somewhat similar to the result of a `docker save` with the notable difference
    that the cfg-blob is discarded.
    This function is useful to e.g. upload file system contents of an oci-container-image to some
    scanning-tool (provided it supports the extraction of tar-archives)
    If include_config_blob is set to False the config blob will be ignored.
    '''
    manifest = oci_client.manifest(image_reference=image_reference)
    offset = 0
    for blob in manifest.blobs() if include_config_blob else manifest.layers:
        logger.debug(f'getting blob {blob.digest}')
        if not include_config_blob:
            logger.debug('skipping config blob')
        tarinfo = tarfile.TarInfo(name=blob.digest + '.tar') # note: may be gzipped
        tarinfo.size = blob.size
        tarinfo.offset = offset
        tarinfo.offset_data = offset + tarfile.BLOCKSIZE

        offset += blob.size + tarfile.BLOCKSIZE

        tarinfo_bytes = tarinfo.tobuf()
        yield tarinfo_bytes

        uploaded_bytes = len(tarinfo_bytes)
        for chunk in oci_client.blob(
            image_reference=image_reference,
            digest=blob.digest,
            stream=True,
            ).iter_content(chunk_size=chunk_size):
            uploaded_bytes += len(chunk)
            yield chunk

        # need to pad full blocks w/ NUL-bytes
        if (missing := tarfile.BLOCKSIZE - (uploaded_bytes % tarfile.BLOCKSIZE)):
            offset += missing
            yield tarfile.NUL * missing

    # tarchives should be terminated w/ two empty blocks
    yield tarfile.NUL * tarfile.BLOCKSIZE * 2
