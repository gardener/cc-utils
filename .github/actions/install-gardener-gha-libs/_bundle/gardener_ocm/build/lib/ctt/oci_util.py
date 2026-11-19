# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0


import collections.abc
import dataclasses
import fnmatch
import hashlib
import io
import json
import logging
import os
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

import ctt.model

logger = logging.getLogger(__name__)


def tar_stream_from_files(
    src_path: str,
    tgt_path: str,
    finalise: bool=True,
) -> collections.abc.Generator[bytes, None, None]:
    '''
    Streams a non-gzipped tar archive containing `src_path` (file or directory) such that:
    - src_path is a file -> single entry at `tgt_path`
    - src_path is a directory -> tree is replicated below `tgt_path`
    All tar entries use uid=gid=0, deterministic modes (0644/0755) and mtime=0 (reproducible).
    `tgt_path` must not contain `..` (already validated by caller).
    `finalise=False` omits the trailer so multiple streams can be chained.
    '''
    buf = io.BytesIO()
    entries: list[tuple[str, str | None]] = [] # (tgt_path, abs_src_path | None for dir)

    if os.path.isfile(src_path):
        entries.append((tgt_path, src_path))
    elif os.path.isdir(src_path):
        for dirpath, dirnames, filenames in os.walk(src_path):
            rel_dir = os.path.relpath(dirpath, src_path)
            if rel_dir == '.':
                rel_dir = ''
            if rel_dir:
                entries.append((os.path.join(tgt_path, rel_dir), None))
            for d in sorted(dirnames):
                tgt_dir_path = os.path.join(tgt_path, rel_dir, d)
                entries.append((tgt_dir_path, None))
            for f in sorted(filenames):
                abs_f = os.path.join(dirpath, f)
                tgt_f_path = os.path.join(tgt_path, rel_dir, f)
                entries.append((tgt_f_path, abs_f))
    else:
        raise ValueError(f'{src_path=} is neither a file nor a directory')

    dedup: dict[str, tuple[str | None, bytes, int]] = {}
    for tgt, src in entries:
        norm = tgt.lstrip('/')
        if src is None:
            ti = tarfile.TarInfo(name=norm)
            ti.type = tarfile.DIRTYPE
            ti.mode = 0o755
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ''
            ti.mtime = 0
            ti.size = 0
            dedup[norm] = (None, ti.tobuf(), 0)
        else:
            size = os.path.getsize(src)
            ti = tarfile.TarInfo(name=norm)
            ti.type = tarfile.REGTYPE
            ti.mode = 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ''
            ti.mtime = 0
            ti.size = size
            dedup[norm] = (src, ti.tobuf(), size)

    for norm in sorted(dedup):
        src, hdr, size = dedup[norm]
        buf.write(hdr)
        if src is not None:
            with open(src, 'rb') as f:
                while chunk := f.read(64 * 1024):
                    buf.write(chunk)
            remainder = size % 512
            if remainder:
                buf.write(b'\0' * (512 - remainder))

    if finalise:
        buf.write(b'\0' * (tarfile.BLOCKSIZE * 2))

    buf.seek(0)
    while chunk := buf.read(64 * 1024):
        yield chunk


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


def _build_append_layer_blob(
    append_files: typing.Sequence[ctt.model.AppendFilesEntry],
) -> tuple[bytes, str, str]:
    '''
    Builds a single gzipped tar blob containing all files from `append_files`.
    Returns (gzipped_blob_bytes, gzipped_digest_with_alg, non_gzipped_digest_with_alg).
    Deterministic: tar entries use uid=gid=0, mtime=0 and stable order.
    '''
    combined_tar_buf = io.BytesIO()
    for entry in append_files:
        for chunk in tar_stream_from_files(entry.src_path, entry.tgt_path, finalise=False):
            combined_tar_buf.write(chunk)
    combined_tar_buf.write(b'\0' * (tarfile.BLOCKSIZE * 2))
    tar_payload = combined_tar_buf.getvalue()

    non_gzipped_digest = 'sha256:' + hashlib.sha256(tar_payload).hexdigest()
    crc = zlib.crc32(tar_payload)

    compressor = gziputil.zlib_compressobj()
    parts = [
        gziputil.gzip_header(fname=b'layer.tar'),
        compressor.compress(tar_payload),
        compressor.flush(),
        gziputil.gzip_footer(crc32=crc, uncompressed_size=len(tar_payload)),
    ]
    gz_blob = b''.join(parts)
    gz_digest = 'sha256:' + hashlib.sha256(gz_blob).hexdigest()

    return gz_blob, gz_digest, non_gzipped_digest


def _append_new_layer_only(
    source_ref: str,
    target_ref: om.OciImageReference,
    manifest: om.OciImageManifest,
    append_files: typing.Sequence[ctt.model.AppendFilesEntry],
    oci_client: oc.Client,
    oci_manifest_annotations: dict[str, str] | None,
) -> typing.Tuple[requests.Response, str, bytes]:
    '''
    Fast-path for pure NEW_LAYER append (no remove_files): replicate blobs as-is,
    append one fresh layer at the end, patch cfg-blob's diff_ids (append our new
    diff_id) and re-upload manifest.

    Avoids the per-layer rewrite that the regular path has to do.
    '''
    src_ref_str = str(source_ref)

    # copy existing blobs (cfg + layers) as-is
    blobs = [manifest.config] + list(manifest.layers)
    for blob_ref in blobs:
        if oci_client.head_blob(image_reference=target_ref, digest=blob_ref.digest, absent_ok=True):
            continue
        blob = oci_client.blob(image_reference=src_ref_str, digest=blob_ref.digest, stream=True)
        oci_client.put_blob(
            image_reference=target_ref,
            digest=blob_ref.digest,
            octets_count=blob_ref.size,
            data=blob,
        )

    # build new layer blob and upload
    gz_blob, gz_digest, non_gzipped_digest = _build_append_layer_blob(append_files)
    oci_client.put_blob(
        image_reference=str(target_ref),
        digest=gz_digest,
        octets_count=len(gz_blob),
        data=gz_blob,
    )

    # patch cfg-blob: append new diff_id
    cfg_bytes = oci_client.blob(
        image_reference=src_ref_str,
        digest=manifest.config.digest,
        stream=False,
    ).content
    cfg = json.loads(cfg_bytes)
    if 'rootfs' not in cfg:
        raise ValueError('expected attr `rootfs` not present on cfg-blob')
    cfg['rootfs']['diff_ids'].append(non_gzipped_digest)
    cfg_blob = json.dumps(cfg).encode('utf-8')

    cfg_digest = f'sha256:{hashlib.sha256(cfg_blob).hexdigest()}'
    oci_client.put_blob(
        image_reference=str(target_ref),
        digest=cfg_digest,
        octets_count=len(cfg_blob),
        data=cfg_blob,
    )

    manifest.config = dataclasses.replace(manifest.config, digest=cfg_digest, size=len(cfg_blob))
    manifest.layers = list(manifest.layers) + [
        om.OciBlobRef(
            digest=gz_digest,
            mediaType='application/vnd.docker.image.rootfs.diff.tar.gzip',
            size=len(gz_blob),
        ),
    ]

    manifest_raw = json.dumps(manifest.as_dict()).encode('utf-8')

    if oci_manifest_annotations:
        manifest_dict = json.loads(manifest_raw)
        manifest_dict.setdefault('annotations', {}).update(oci_manifest_annotations)
        manifest_raw = json.dumps(manifest_dict).encode('utf-8')

    manifest_digest = hashlib.sha256(manifest_raw).hexdigest()
    target_ref = target_ref.with_new_digest(digest=manifest_digest)

    res = oci_client.put_manifest(
        image_reference=target_ref,
        manifest=manifest_raw,
    )
    res.raise_for_status()

    return res, target_ref, manifest_raw


def filter_image(
    source_ref: typing.Union[str, om.OciImageReference],
    target_ref: typing.Union[str, om.OciImageReference],
    oci_client: oc.Client,
    remove_files: typing.Sequence[str]=(),
    remove_file_globs: typing.Sequence[str]=(),
    strip_manifest_attributes: typing.Sequence[str]=(),
    append_files: typing.Sequence[ctt.model.AppendFilesEntry]=(),
    append_mode: ctt.model.AppendMode=ctt.model.AppendMode.REPLACE_REMOVED,
    append_layer_idx: int | None=None,
    mode: oci.ReplicationMode=oci.ReplicationMode.REGISTRY_DEFAULTS,
    platform_filter: typing.Callable[[om.OciPlatform], bool]=None,
    oci_manifest_annotations: dict[str, str]=None,
) -> typing.Tuple[requests.Response, str, bytes]: # response, tgt-ref, manifest_bytes
    source_ref = om.OciImageReference.to_image_ref(source_ref)
    target_ref = om.OciImageReference.to_image_ref(target_ref)

    # shortcut in case there are no filtering-rules
    if (
        not remove_files
        and not remove_file_globs
        and not strip_manifest_attributes
        and not append_files
    ):
        return oci.replicate_artifact(
            src_image_reference=source_ref,
            tgt_image_reference=target_ref,
            oci_client=oci_client,
            mode=mode,
            platform_filter=platform_filter,
            annotations=oci_manifest_annotations,
        )

    if strip_manifest_attributes:
        if remove_files or remove_file_globs or append_files:
            raise NotImplementedError(
                'cannot combine removal/append of in-image files with stripping of manifest '
                'attributes'
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
                remove_file_globs=remove_file_globs,
                append_files=append_files,
                append_mode=append_mode,
                append_layer_idx=append_layer_idx,
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
            remove_file_globs=remove_file_globs,
            append_files=append_files,
            append_mode=append_mode,
            append_layer_idx=append_layer_idx,
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
    remove_file_globs = [p.lstrip('/') for p in remove_file_globs]

    # resolve REPLACE_REMOVED to NEW_LAYER when no removal was requested (nothing to replace)
    if (
        append_files
        and append_mode is ctt.model.AppendMode.REPLACE_REMOVED
        and not remove_files
        and not remove_file_globs
    ):
        append_mode = ctt.model.AppendMode.NEW_LAYER

    # fast path for pure NEW_LAYER append: skip per-layer rewrite entirely; just copy
    # blobs as-is and append a fresh layer at the end.
    if (
        append_files
        and append_mode is ctt.model.AppendMode.NEW_LAYER
        and not remove_files
        and not remove_file_globs
    ):
        return _append_new_layer_only(
            source_ref=source_ref,
            target_ref=target_ref,
            manifest=manifest,
            append_files=append_files,
            oci_client=oci_client,
            oci_manifest_annotations=oci_manifest_annotations,
        )

    if append_files:
        if append_mode is ctt.model.AppendMode.INSERT_INTO_LAYER:
            if append_layer_idx is None:
                raise ValueError('append_layer_idx required for INSERT_INTO_LAYER')
            resolved_idx = (
                append_layer_idx
                if append_layer_idx >= 0
                else len(manifest.layers) + append_layer_idx
            )
            if not 0 <= resolved_idx < len(manifest.layers):
                raise ValueError(
                    f'{append_layer_idx=} out of range for {len(manifest.layers)=}'
                )
            append_target_layer_idxs = {resolved_idx}
        elif append_mode is ctt.model.AppendMode.REPLACE_REMOVED:
            append_target_layer_idxs = None # decided per-layer, after stream is consumed
        elif append_mode is ctt.model.AppendMode.NEW_LAYER:
            # reachable only when remove_files is non-empty; NEW_LAYER executes after the loop
            append_target_layer_idxs = set()
        else:
            raise NotImplementedError(append_mode)
    else:
        append_target_layer_idxs = set()

    # mutates per-layer; reset per layer-iteration
    removed_from_current_layer = [False]
    removed_paths_in_current_layer = [set()]
    # set to True once at least one append has actually been emitted into a layer
    appended_into_layer = [False]

    def tarmember_filter(tar_info: tarfile.TarInfo):
        stripped_name = tar_info.name.lstrip('./')
        if stripped_name in remove_files:
            logger.debug(f'rm: {tar_info.name=}')
            removed_from_current_layer[0] = True
            removed_paths_in_current_layer[0].add(stripped_name)
            return False # rm member
        for glob in remove_file_globs:
            if fnmatch.fnmatchcase(stripped_name, glob):
                logger.debug(f'rm (glob {glob=}): {tar_info.name=}')
                removed_from_current_layer[0] = True
                removed_paths_in_current_layer[0].add(stripped_name)
                return False # rm member
        return True # keep member

    def _iter_append_payload_streams(
        tgt_filter: collections.abc.Callable[[str], bool] | None=None,
    ) -> collections.abc.Generator[bytes, None, None]:
        '''
        Chains the per-entry tar streams into one member-stream (no trailer). Border
        between consecutive entries needs no padding beyond 512-alignment, which each
        entry-stream maintains internally.

        When `tgt_filter` is provided, only entries whose `tgt_path` passes the filter
        are emitted. This implements per-path REPLACE_REMOVED matching: each append
        entry lands only in layers where the corresponding target was actually removed,
        avoiding duplicate-path situations when a base image already shipped a file at
        `tgt_path` that did NOT match any of the remove patterns.
        '''
        for entry in append_files:
            if tgt_filter is not None and not tgt_filter(entry.tgt_path.lstrip('/')):
                continue
            yield from tar_stream_from_files(entry.src_path, entry.tgt_path, finalise=False)

    _TRAILER = b'\0' * (tarfile.BLOCKSIZE * 2)

    def _stream_with_appends(
        filtered_stream,
        should_append_getter,
        append_tgt_filter: collections.abc.Callable[[str], bool] | None=None,
    ):
        '''
        Chains the filtered (removefiles) stream with the append payload. Holder of the
        append-decision is consulted exactly once: when filtered_stream is exhausted.
        At that point the TAR trailer has not yet been emitted (filtered_tarfile_generator
        is constructed with finalise=False), so appending stays a pure concatenation —
        no buffering needed to strip a trailer retroactively. The trailer is emitted
        exactly once, either here (no append) or after the last append-entry.

        `append_tgt_filter` is forwarded to `_iter_append_payload_streams`: per-layer,
        it restricts which AppendFiles entries are written (`REPLACE_REMOVED` uses it
        so each entry lands only in layers where its `tgt_path` was removed).
        '''
        yield from filtered_stream
        if not append_files or not should_append_getter():
            yield _TRAILER
            return

        emitted_any = [False]
        stream = _iter_append_payload_streams(tgt_filter=append_tgt_filter)
        for chunk in stream:
            emitted_any[0] = True
            yield chunk
        # even if the per-path filter suppressed all entries, the trailer still goes
        # below the layer's filtered payload — we must not skip it (a tar reader
        # depends on seeing the trailing two zero blocks at the very end).
        yield _TRAILER
        if emitted_any[0]:
            appended_into_layer[0] = True

    # prepare copy of layers to avoid modification while iterating
    layers_copy = manifest.layers.copy()

    non_gzipped_layer_digests = {} # {gzipped-digest: sha256:non-gzipped-digest}

    have_non_tar_layer = False
    patch_cfg_blob = True
    for layer_idx, layer in enumerate(manifest.layers):
        removed_from_current_layer[0] = False
        removed_paths_in_current_layer[0] = set()
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
                finalise=False, # trailer is emitted by _stream_with_appends
            )

            f.write((gzip_header := gziputil.gzip_header(fname=b'layer.tar')))
            layer_hash.update(gzip_header)
            leng += len(gzip_header)

            compressor = gziputil.zlib_compressobj()

            # decide whether this layer should have entries appended, and if so with
            # which per-path filter.
            # - INSERT_INTO_LAYER: matches configured idx (normalised for negatives); all entries
            # - REPLACE_REMOVED:   decided after the stream has been consumed, based on
            #                      whether tarmember_filter rejected anything in this layer;
            #                      per-path filter restricts appends to entries whose tgt_path
            #                      was actually removed HERE
            # - NEW_LAYER:         never matches (extra layer is appended after the loop)
            if append_mode is ctt.model.AppendMode.INSERT_INTO_LAYER:
                should_append = layer_idx in append_target_layer_idxs
                should_append_getter = lambda: should_append
                append_tgt_filter = None
            elif append_mode is ctt.model.AppendMode.REPLACE_REMOVED:
                should_append_getter = lambda: removed_from_current_layer[0]
                append_tgt_filter = lambda tgt: tgt in removed_paths_in_current_layer[0]
            else: # NEW_LAYER
                should_append_getter = lambda: False
                append_tgt_filter = None

            stream_with_appends = _stream_with_appends(
                filtered_stream,
                should_append_getter,
                append_tgt_filter=append_tgt_filter,
            )

            for chunk in stream_with_appends:
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

    # append mode NEW_LAYER (reachable only when remove_files is non-empty AND user
    # requested NEW_LAYER semantics): build a fresh tar-layer containing the new entries
    # and append it at the end. Use REPLACE_REMOVED fallback if no layer actually matched.
    if append_files and not appended_into_layer[0]:
        gz_blob, gz_digest, non_gzipped_digest = _build_append_layer_blob(append_files)

        oci_client.put_blob(
            image_reference=str(target_ref),
            digest=gz_digest,
            octets_count=len(gz_blob),
            data=gz_blob,
        )

        manifest.layers = list(manifest.layers) + [
            om.OciBlobRef(
                digest=gz_digest,
                mediaType='application/vnd.docker.image.rootfs.diff.tar.gzip',
                size=len(gz_blob),
            ),
        ]
        non_gzipped_layer_digests[gz_digest] = non_gzipped_digest
        appended_into_layer[0] = True

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
