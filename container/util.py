# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import dataclasses
import hashlib
import json
import logging
import tarfile
import tempfile
import typing
import zlib

import requests

import ccc.oci
import gziputil
import oci
import oci.client as oc
import oci.convert as oconv
import oci.platform
import oci.model as om
import tarutil

logger = logging.getLogger(__name__)


def image_exists(image_reference: str):
    oci_client = ccc.oci.oci_client()
    return bool(oci_client.head_manifest(image_reference=image_reference, absent_ok=True))


def filter_image(
    source_ref: typing.Union[str, om.OciImageReference],
    target_ref: typing.Union[str, om.OciImageReference],
    remove_files: typing.Sequence[str]=(),
    oci_client: oc.Client=None,
    mode: oci.ReplicationMode=oci.ReplicationMode.REGISTRY_DEFAULTS,
    platform_filter: typing.Callable[[om.OciPlatform], bool]=None,
    oci_manifest_annotations: dict[str, str]=None,
) -> typing.Tuple[requests.Response, str, bytes]: # response, tgt-ref, manifest_bytes
    if not oci_client:
        oci_client = ccc.oci.oci_client()

    source_ref = om.OciImageReference.to_image_ref(source_ref)
    target_ref = om.OciImageReference.to_image_ref(target_ref)

    # shortcut in case there are no filtering-rules
    if not remove_files:
        return oci.replicate_artifact(
            src_image_reference=source_ref,
            tgt_image_reference=target_ref,
            oci_client=oci_client,
            mode=mode,
            platform_filter=platform_filter,
            annotations=oci_manifest_annotations,
        )

    if mode is oci.ReplicationMode.REGISTRY_DEFAULTS:
        accept = None
    elif mode is oci.ReplicationMode.PREFER_MULTIARCH:
        accept = om.MimeTypes.prefer_multiarch
    elif mode is oci.ReplicationMode.NORMALISE_TO_MULTIARCH:
        accept = om.MimeTypes.prefer_multiarch
    else:
        raise NotImplementedError(mode)

    manifest = oci_client.manifest(
        image_reference=str(source_ref),
        accept=accept,
    )

    if isinstance(manifest, om.OciImageManifestList):
        # recurse into sub-images

        src_name = source_ref.ref_without_tag
        tgt_name = target_ref.ref_without_tag

        patched_manifests = []
        for sub_manifest in tuple(manifest.manifests):
            source_ref = f'{src_name}@{sub_manifest.digest}'

            if platform_filter:
                platform = oci.platform.from_single_image(
                    image_reference=source_ref,
                    oci_client=oci_client,
                    base_platform=sub_manifest.platform,
                )
                if not platform_filter(platform):
                    logger.info(f'skipping {platform=} for {source_ref=}')
                    manifest.manifests.remove(sub_manifest)
                    continue

            logger.info(f'filtering to {tgt_name=}')

            res, tgt_ref, manifest_bytes = filter_image(
                source_ref=source_ref,
                target_ref=tgt_name,
                remove_files=remove_files,
                oci_client=oci_client,
                oci_manifest_annotations=oci_manifest_annotations,
            )

            # patch (potentially) modified manifest-digest
            patched_manifest = dataclasses.replace(
                sub_manifest,
                digest=f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}',
                size=len(manifest_bytes),
            )
            patched_manifests.append(patched_manifest)

        manifest.manifests = patched_manifests
        manifest_dict = manifest.as_dict()
        manifest_raw = json.dumps(manifest_dict).encode('utf-8')
        res = oci_client.put_manifest(
            image_reference=str(target_ref),
            manifest=manifest_raw,
        )

        return res, str(target_ref), manifest_raw

    # normalise single-image to multi-arch (w/ one entry)
    if mode is oci.ReplicationMode.NORMALISE_TO_MULTIARCH:
        if not source_ref.has_digest_tag:
            source_ref = om.OciImageReference.to_image_ref(
                oci_client.to_digest_hash(
                    image_reference=source_ref,
                )
            )

        platform = oci.platform.from_single_image(
            image_reference=source_ref,
            oci_client=oci_client,
        )

        res, ref, manifest_bytes = filter_image(
            source_ref=source_ref,
            target_ref=target_ref.ref_without_tag,
            remove_files=remove_files,
            oci_client=oci_client,
            oci_manifest_annotations=oci_manifest_annotations,
        )

        manifest_list = om.OciImageManifestList(
            manifests=[
                om.OciImageManifestListEntry(
                    digest=f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}',
                    mediaType=manifest.mediaType,
                    size=len(manifest_bytes),
                    platform=platform,
                )
            ],
        )

        manifest_list_bytes = json.dumps(
            manifest_list.as_dict(),
        ).encode('utf-8')

        res = oci_client.put_manifest(
            image_reference=target_ref,
            manifest=manifest_list_bytes,
        )

        return res, target_ref, manifest_list_bytes

    cp_cfg_blob = True
    if isinstance(manifest, om.OciImageManifestV1):
        logger.info(f'converting v1-manifest -> v2 {source_ref=} {target_ref=}')
        manifest, cfg_blob = oconv.v1_manifest_to_v2(
            manifest=manifest,
            oci_client=oci_client,
            tgt_image_ref=str(target_ref),
        )
        cp_cfg_blob = False # we synthesise new cfg - thus we cannot cp from src
    elif not isinstance(manifest, om.OciImageManifest):
        raise NotImplementedError(manifest)

    # allow / ignore leading '/'
    remove_files = [p.lstrip('/') for p in remove_files]

    def tarmember_filter(tar_info: tarfile.TarInfo):
        stripped_name = tar_info.name.lstrip('./')
        if stripped_name in remove_files:
            logger.debug(f'rm: {tar_info.name=}')
            return False # rm member
        return True # keep member

    # prepare copy of layers to avoid modification while iterating
    layers_copy = manifest.layers.copy()

    non_gzipped_layer_digests = {} # {gzipped-digest: sha256:non-gzipped-digest}

    have_non_tar_layer = False
    patch_cfg_blob = True
    for layer in manifest.layers:
        layer_hash = hashlib.sha256()
        cfg_hash = hashlib.sha256() # we need to write "non-gzipped" hash to cfg-blob
        leng = 0
        src_leng = 0 # required for calculating leng for gzip-footer
        crc = 0 # requried for calculcating crc32-checksum for gzip-footer

        if not 'tar' in layer.mediaType:
            have_non_tar_layer = True
            cp_cfg_blob = True
            patch_cfg_blob = False

            # special-case: do not filter "layer", if it is not a tar (e.g. the case for
            # "in-toto" (application/vnd.in-toto+json)
            if oci_client.head_blob(
                image_reference=target_ref,
                digest=layer.digest,
                absent_ok=True,
            ):
                continue # skip blob replication if already present in tgt

            blob = oci_client.blob(
                image_reference=str(source_ref),
                digest=layer.digest,
                stream=True,
            )
            oci_client.put_blob(
                image_reference=target_ref,
                digest=layer.digest,
                octets_count=layer.size,
                data=blob,
            )
            continue

        if have_non_tar_layer:
            raise RuntimeError(
                'don\'t know how to process mixed image (tar + non-tar layers)'
            )

        # unfortunately, GCR (our most important oci-registry) does not support chunked uploads,
        # so we have to resort to writing the streaming result into a local tempfile to be able
        # to calculate digest-hash prior to upload to tgt; XXX: we might use streaming
        # when interacting w/ oci-registries that support chunked-uploads
        with tempfile.TemporaryFile() as f:
            src_tar_stream = oci_client.blob(
                image_reference=str(source_ref),
                digest=layer.digest,
                stream=True,
            ).iter_content(chunk_size=tarfile.BLOCKSIZE * 64)
            src_tar_fobj = tarutil.FilelikeProxy(generator=src_tar_stream)
            filtered_stream = tarutil.filtered_tarfile_generator(
                src_tf=tarfile.open(fileobj=src_tar_fobj, mode='r|*'),
                filter_func=tarmember_filter,
                chunk_size=tarfile.BLOCKSIZE * 64,
            )

            f.write((gzip_header := gziputil.gzip_header(fname=b'layer.tar')))
            layer_hash.update(gzip_header)
            leng += len(gzip_header)

            compressor = gziputil.zlib_compressobj()

            for chunk in filtered_stream:
                cfg_hash.update(chunk) # need to hash before compressing for cfg-blob
                crc = zlib.crc32(chunk, crc)
                src_leng += len(chunk)

                chunk = compressor.compress(chunk)
                layer_hash.update(chunk)
                leng += len(chunk)
                f.write(chunk)

            f.write((remainder := compressor.flush()))
            layer_hash.update(remainder)
            leng += len(remainder)

            gzip_footer = gziputil.gzip_footer(
                crc32=crc,
                uncompressed_size=src_leng,
            )
            f.write(gzip_footer)
            layer_hash.update(gzip_footer)
            leng += len(gzip_footer)

            f.seek(0)

            oci_client.put_blob(
                image_reference=target_ref,
                digest=(layer_digest := 'sha256:' + layer_hash.hexdigest()),
                octets_count=leng,
                data=f,
            )

            non_gzipped_layer_digests[layer_digest] = 'sha256:' + cfg_hash.hexdigest()

            # update copy of layers-list with new layer
            new_layer = dataclasses.replace(layer, digest=layer_digest, size=leng)
            layers_copy[layers_copy.index(layer)] = new_layer

    # switch layers in manifest to announce changes w/ manifest-upload
    manifest.layers = layers_copy

    # need to patch cfg-object, in case layer-digests changed
    if cp_cfg_blob:
        cfg_blob = oci_client.blob(
            image_reference=str(source_ref),
            digest=manifest.config.digest,
            stream=False,
        ).content # cfg-blobs are small - no point in streaming
    else:
        cfg_blob = json.loads(cfg_blob)

    if patch_cfg_blob:
        if isinstance(cfg_blob, bytes):
            cfg_blob = json.loads(cfg_blob)

        if not 'rootfs' in cfg_blob:
            raise ValueError('expected attr `rootfs` not present on cfg-blob')
        cfg_blob['rootfs'] = {
            'diff_ids': [
                non_gzipped_layer_digests[layer.digest] for layer in manifest.layers
            ],
            'type': 'layers',
        }

    if isinstance(cfg_blob, dict):
        cfg_blob = json.dumps(cfg_blob).encode('utf-8')

    cfg_digest = f'sha256:{hashlib.sha256(cfg_blob).hexdigest()}'
    cfg_leng = len(cfg_blob)
    oci_client.put_blob(
        image_reference=str(target_ref),
        digest=cfg_digest,
        octets_count=cfg_leng,
        data=cfg_blob,
    )

    manifest.config = dataclasses.replace(manifest.config, digest=cfg_digest, size=cfg_leng)

    manifest_raw = json.dumps(manifest.as_dict()).encode('utf-8')

    if oci_manifest_annotations:
        manifest_dict = json.loads(manifest_raw)
        if not 'annotations' in manifest_dict:
            manifest_dict['annotations'] = {}

        manifest_dict['annotations'] |= oci_manifest_annotations

        manifest_raw = json.dumps(manifest_dict).encode('utf-8')

    if target_ref.has_tag:
        target_ref = str(target_ref)
    else:
        # if tgt does not bear a tag, calculate hash digest as tgt
        manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
        target_ref = f'{target_ref.ref_without_tag}@sha256:{manifest_digest}'

    res = oci_client.put_manifest(
        image_reference=target_ref,
        manifest=manifest_raw
    )
    res.raise_for_status()

    return res, target_ref, manifest_raw
