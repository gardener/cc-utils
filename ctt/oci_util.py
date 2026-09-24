# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import hashlib
import json
import logging
import tarfile
import tempfile
import typing
import zlib

import requests

import gziputil
import oci
import oci.client as oc
import oci.convert as oconv
import oci.platform
import oci.model as om
import tarutil

logger = logging.getLogger(__name__)


def parse_attribute_path(path: str) -> tuple[tuple[str, bool], ...]:
    '''
    Parses a dotted attribute path into segments of (key, is_list). A `key[]` segment
    traverses into each element of the list value at `key`. The last segment must not
    be a list-traversal.
    Example: 'manifests[].platform.features'
    '''
    segments = []
    for segment in path.split('.'):
        is_list = segment.endswith('[]')
        key = segment[:-2] if is_list else segment
        if not key:
            raise ValueError(f'invalid attribute path: {path!r}')
        segments.append((key, is_list))

    if segments[-1][1]:
        raise ValueError(f'last segment of attribute path must not be a list: {path!r}')

    return tuple(segments)


def strip_attribute(
    document: dict,
    attribute_path: tuple[tuple[str, bool], ...], # as parsed by parse_attribute_path
) -> bool:
    '''
    Removes the attribute denoted by attribute_path from document (in-place), tolerating
    its absence (or type-mismatches along the path). Returns whether anything was removed.
    '''
    (key, is_list), rest = attribute_path[0], attribute_path[1:]

    if not isinstance(document, dict) or key not in document:
        return False

    if not rest:
        del document[key]
        return True

    value = document[key]

    if not is_list:
        return strip_attribute(value, rest)

    if not isinstance(value, list):
        return False

    changed = False
    for element in value:
        changed |= strip_attribute(element, rest)
    return changed


def replicate_with_stripped_manifest_attributes(
    source_ref: typing.Union[str, om.OciImageReference],
    target_ref: typing.Union[str, om.OciImageReference],
    strip_manifest_attributes: typing.Sequence[str],
    oci_client: oc.Client,
    mode: oci.ReplicationMode=oci.ReplicationMode.REGISTRY_DEFAULTS,
    platform_filter: typing.Callable[[om.OciPlatform], bool]=None,
    oci_manifest_annotations: dict[str, str]=None,
) -> typing.Tuple[requests.Response, str, bytes]: # response, tgt-ref, manifest_bytes
    '''
    Replicates an OCI artefact, removing the given attributes (dotted paths, see
    parse_attribute_path) from the top-level manifest document prior to pushing. Useful
    for target registries that reject manifests carrying certain (valid, but unsupported)
    attributes (e.g. `manifests[].platform.features`).

    For multiarch images, the image-index (incl. its entries) is rewritten and child
    manifests are replicated verbatim.
    '''
    if mode is oci.ReplicationMode.NORMALISE_TO_MULTIARCH:
        raise NotImplementedError('cannot strip manifest attributes with NORMALISE_TO_MULTIARCH')

    source_ref = om.OciImageReference.to_image_ref(source_ref)
    target_ref = om.OciImageReference.to_image_ref(target_ref)

    attribute_paths = [parse_attribute_path(p) for p in strip_manifest_attributes]

    if mode is oci.ReplicationMode.REGISTRY_DEFAULTS:
        accept = None
    elif mode is oci.ReplicationMode.PREFER_MULTIARCH:
        accept = om.MimeTypes.prefer_multiarch
    else:
        raise NotImplementedError(mode)

    resp = oci_client.manifest_raw(
        image_reference=str(source_ref),
        accept=accept,
    )
    manifest_dict = json.loads(resp.text)
    media_type = manifest_dict.get('mediaType') or resp.headers.get('Content-Type')

    if int(manifest_dict.get('schemaVersion', 2)) == 1:
        raise NotImplementedError('cannot strip manifest attributes of legacy (v1) manifests')

    if media_type in (om.DOCKER_MANIFEST_LIST_MIME, om.OCI_IMAGE_INDEX_MIME):
        # for index-documents, annotations are only retained for OCI media-type
        # (mirrors oci.model.OciImageManifestList.as_dict)
        apply_annotations = media_type == om.OCI_IMAGE_INDEX_MIME

        src_repo = source_ref.ref_without_tag
        tgt_repo = target_ref.ref_without_tag

        # only propagate PREFER_MULTIARCH (preserves nested indices)
        recursive_mode = oci.ReplicationMode.REGISTRY_DEFAULTS
        if mode is oci.ReplicationMode.PREFER_MULTIARCH:
            recursive_mode = oci.ReplicationMode.PREFER_MULTIARCH

        kept_entries = []
        for entry in manifest_dict.get('manifests', ()):
            child_src_ref = f'{src_repo}@{entry["digest"]}'

            if platform_filter:
                platform_raw = entry.get('platform')
                platform = oci.platform.from_single_image(
                    image_reference=child_src_ref,
                    oci_client=oci_client,
                    base_platform=om.OciPlatform(
                        architecture=platform_raw.get('architecture'),
                        os=platform_raw.get('os'),
                        variant=platform_raw.get('variant'),
                        features=platform_raw.get('features'),
                    ) if platform_raw else None,
                )
                if not platform_filter(platform):
                    logger.info(f'skipping {platform=} for {child_src_ref=}')
                    continue

            res, ref, child_bytes = oci.replicate_artifact(
                src_image_reference=child_src_ref,
                tgt_image_reference=f'{tgt_repo}@{entry["digest"]}',
                oci_client=oci_client,
                mode=recursive_mode,
                annotations=oci_manifest_annotations,
            )

            child_digest = f'sha256:{hashlib.sha256(child_bytes).hexdigest()}'
            if child_digest != entry['digest']:
                entry['digest'] = child_digest
                entry['size'] = len(child_bytes)

            kept_entries.append(entry)

        manifest_dict['manifests'] = kept_entries
    elif media_type in (om.OCI_MANIFEST_SCHEMA_V2_MIME, om.DOCKER_MANIFEST_SCHEMA_V2_MIME):
        apply_annotations = True # mirrors oci.model.OciImageManifest.as_dict

        for blob_ref in [manifest_dict.get('config'), *manifest_dict.get('layers', ())]:
            if not blob_ref:
                continue
            digest = blob_ref['digest']
            if oci_client.head_blob(
                image_reference=target_ref,
                digest=digest,
                absent_ok=True,
            ):
                continue
            blob = oci_client.blob(
                image_reference=str(source_ref),
                digest=digest,
            )
            oci_client.put_blob(
                image_reference=target_ref,
                digest=digest,
                octets_count=blob_ref['size'],
                data=blob,
            )
    else:
        raise NotImplementedError(f'{media_type=}')

    for attribute_path in attribute_paths:
        strip_attribute(manifest_dict, attribute_path)

    if oci_manifest_annotations and apply_annotations:
        annotations = manifest_dict.setdefault('annotations', {})
        for key, value in oci_manifest_annotations.items():
            if annotations.get(key) != value:
                annotations[key] = value

    manifest_raw = json.dumps(manifest_dict).encode('utf-8')
    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    target_ref = target_ref.with_new_digest(digest=manifest_digest)

    res = oci_client.put_manifest(
        image_reference=target_ref,
        manifest=manifest_raw,
    )

    return res, str(target_ref), manifest_raw


def filter_image(
    source_ref: typing.Union[str, om.OciImageReference],
    target_ref: typing.Union[str, om.OciImageReference],
    oci_client: oc.Client,
    remove_files: typing.Sequence[str]=(),
    strip_manifest_attributes: typing.Sequence[str]=(),
    mode: oci.ReplicationMode=oci.ReplicationMode.REGISTRY_DEFAULTS,
    platform_filter: typing.Callable[[om.OciPlatform], bool]=None,
    oci_manifest_annotations: dict[str, str]=None,
) -> typing.Tuple[requests.Response, str, bytes]: # response, tgt-ref, manifest_bytes
    source_ref = om.OciImageReference.to_image_ref(source_ref)
    target_ref = om.OciImageReference.to_image_ref(target_ref)

    # shortcut in case there are no filtering-rules
    if not remove_files and not strip_manifest_attributes:
        return oci.replicate_artifact(
            src_image_reference=source_ref,
            tgt_image_reference=target_ref,
            oci_client=oci_client,
            mode=mode,
            platform_filter=platform_filter,
            annotations=oci_manifest_annotations,
        )

    if strip_manifest_attributes:
        if remove_files:
            raise NotImplementedError(
                'cannot combine removal of in-image files and stripping of manifest attributes'
            )
        return replicate_with_stripped_manifest_attributes(
            source_ref=source_ref,
            target_ref=target_ref,
            strip_manifest_attributes=strip_manifest_attributes,
            oci_client=oci_client,
            mode=mode,
            platform_filter=platform_filter,
            oci_manifest_annotations=oci_manifest_annotations,
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

        manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
        target_ref = target_ref.with_new_digest(digest=manifest_digest)

        res = oci_client.put_manifest(
            image_reference=target_ref,
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

        manifest_list_digest = hashlib.sha256(manifest_list_bytes).hexdigest()
        target_ref = target_ref.with_new_digest(digest=manifest_list_digest)

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

    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    target_ref = target_ref.with_new_digest(digest=manifest_digest)

    res = oci_client.put_manifest(
        image_reference=target_ref,
        manifest=manifest_raw
    )
    res.raise_for_status()

    return res, target_ref, manifest_raw
