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

import deprecated

import ccc.oci
import ci.util
import oci
import oci.client as oc
import oci.model as om
import tarutil

logger = logging.getLogger(__name__)


def image_exists(image_reference: str):
    oci_client = ccc.oci.oci_client()
    return bool(oci_client.head_manifest(image_reference=image_reference, absent_ok=True))


def filter_image(
    source_ref:str,
    target_ref:str,
    remove_files: typing.Sequence[str]=(),
    oci_client: oc.Client=None,
):
    if not oci_client:
        oci_client = ccc.oci.oci_client()

    # shortcut in case there are no filtering-rules
    if not remove_files:
        return oci.replicate_artifact(
            src_image_reference=source_ref,
            tgt_image_reference=target_ref,
            oci_client=oci_client,
        )

    manifest = oci_client.manifest(image_reference=source_ref)

    if not isinstance(manifest, om.OciImageManifest):
        raise NotImplementedError(manifest)

    # allow / ignore leading '/'
    remove_files = [p.lstrip('/') for p in remove_files]

    def tarmember_filter(tar_info: tarfile.TarInfo):
        if tar_info.name in remove_files:
            return False # rm member
        return True # keep member

    # prepare copy of layers to avoid modification while iterating
    layers_copy = manifest.layers.copy()

    for layer in manifest.layers:
        layer_hash = hashlib.sha256()
        leng = 0

        # unfortunately, GCR (our most important oci-registry) does not support chunked uploads,
        # so we have to resort to writing the streaming result into a local tempfile to be able
        # to calculate digest-hash prior to upload to tgt; XXX: we might use streaming
        # when interacting w/ oci-registries that support chunked-uploads
        with tempfile.TemporaryFile() as f:
            src_tar_stream = oci_client.blob(
                image_reference=source_ref,
                digest=layer.digest,
                stream=True,
            ).iter_content(chunk_size=tarfile.BLOCKSIZE)
            src_tar_fobj = tarutil._FilelikeProxy(generator=src_tar_stream)
            filtered_stream = tarutil.filtered_tarfile_generator(
                src_tf=tarfile.open(fileobj=src_tar_fobj, mode='r|*'),
                filter_func=tarmember_filter,
            )

            for chunk in filtered_stream:
                layer_hash.update(chunk)
                leng += len(chunk)
                f.write(chunk)

            f.seek(0)

            oci_client.put_blob(
                image_reference=target_ref,
                digest=(layer_digest := 'sha256:' + layer_hash.hexdigest()),
                octets_count=leng,
                data=f,
            )

            # update copy of layers-list with new layer
            new_layer = dataclasses.replace(layer, digest=layer_digest, size=leng)
            layers_copy[layers_copy.index(layer)] = new_layer

    # switch layers in manifest to announce changes w/ manifest-upload
    manifest.layers = layers_copy

    # need to patch cfg-object, in case layer-digests changed
    cfg_blob = oci_client.blob(
        image_reference=source_ref,
        digest=manifest.config.digest,
        stream=False,
    ).json() # cfg-blobs are small - no point in streaming
    if not 'rootfs' in cfg_blob:
        raise ValueError('expected attr `rootfs` not present on cfg-blob')

    cfg_blob['rootfs'] = {
        'diff_ids': [
            layer.digest for layer in manifest.layers
        ],
        'type': 'layers',
    }
    cfg_blob = json.dumps(cfg_blob).encode('utf-8')
    cfg_digest = f'sha256:{hashlib.sha256(cfg_blob).hexdigest()}'
    cfg_leng = len(cfg_blob)
    oci_client.put_blob(
        image_reference=target_ref,
        digest=cfg_digest,
        octets_count=cfg_leng,
        data=cfg_blob,
    )

    manifest.config = dataclasses.replace(manifest.config, digest=cfg_digest, size=cfg_leng)

    manifest_raw = json.dumps(dataclasses.asdict(manifest)).encode('utf-8')

    oci_client.put_manifest(image_reference=target_ref, manifest=manifest_raw)


@deprecated.deprecated
def filter_container_image(
    image_file,
    out_file,
    remove_entries,
):
    ci.util.existing_file(image_file)
    if not remove_entries:
        raise ValueError('remove_entries must not be empty')
    # allow absolute paths
    remove_entries = [e.lstrip('/') for e in remove_entries]

    with tarfile.open(image_file) as tf:
        manifest = json.load(tf.extractfile('manifest.json'))
        if not len(manifest) == 1:
            raise NotImplementedError()
        manifest = manifest[0]
        cfg_name = manifest['Config']

    with tarfile.open(image_file, 'r') as in_tf, tarfile.open(out_file, 'w') as out_tf:
        _filter_files(
            manifest=manifest,
            cfg_name=cfg_name,
            in_tarfile=in_tf,
            out_tarfile=out_tf,
            remove_entries=set(remove_entries),
        )


@deprecated.deprecated
def _filter_files(
    manifest,
    cfg_name,
    in_tarfile: tarfile.TarFile,
    out_tarfile: tarfile.TarFile,
    remove_entries,
):
    layer_paths = set(manifest['Layers'])
    changed_layer_hashes = [] # [(old, new),]

    # copy everything that does not need to be patched
    for tar_info in in_tarfile:
        if not tar_info.isfile():
            out_tarfile.addfile(tar_info)
            continue

        # cfg needs to be rewritten - so do not cp
        if tar_info.name in (cfg_name, 'manifest.json'):
            continue

        fileobj = in_tarfile.extractfile(tar_info)

        if tar_info.name not in layer_paths:
            out_tarfile.addfile(tar_info, fileobj=fileobj)
            continue

        # assumption: layers are always tarfiles
        # check if we need to patch
        layer_tar = tarfile.open(fileobj=fileobj)
        # normalise paths
        layer_tar_paths = {
            path.lstrip('./') for path in layer_tar.getnames()
        }
        have_match = bool(layer_tar_paths & remove_entries)
        fileobj.seek(0)

        if not have_match:
            out_tarfile.addfile(tar_info, fileobj=fileobj)
        else:
            old_hash = hashlib.sha256() # XXX hard-code hash algorithm for now
            while fileobj.peek():
                old_hash.update(fileobj.read(2048))
            fileobj.seek(0)

            patched_tar, size = _filter_single_tar(
                in_file=layer_tar,
                remove_entries=remove_entries,
            )
            # patch tar_info to reduced size
            tar_info.size = size

            new_hash = hashlib.sha256() # XXX hard-code hash algorithm for now
            while patched_tar.peek():
                new_hash.update(patched_tar.read(2048))
            patched_tar.seek(0)

            out_tarfile.addfile(tar_info, fileobj=patched_tar)
            logging.debug(f'patched: {tar_info.name}')

            changed_layer_hashes.append((old_hash.hexdigest(), new_hash.hexdigest()))

    # update cfg
    cfg = json.load(in_tarfile.extractfile(cfg_name))
    root_fs = cfg['rootfs']
    if not root_fs['type'] == 'layers':
        raise NotImplementedError()
    # XXX hard-code hash algorithm (assume all entries are prefixed w/ sha256)
    diff_ids = root_fs['diff_ids']
    for old_hash, new_hash in changed_layer_hashes:
        idx = diff_ids.index('sha256:' + old_hash)
        diff_ids[idx] = 'sha256:' + new_hash

    # hash cfg again (as its name is derived from its hash)
    cfg_raw = json.dumps(cfg)
    cfg_hash = hashlib.sha256(cfg_raw.encode('utf-8')).hexdigest()
    cfg_name = cfg_hash + '.json'

    # add cfg to resulting archive
    # unfortunately, tarfile requires us to use a tempfile :-(
    with tempfile.TemporaryFile() as tmp_fh:
        tmp_fh.write(cfg_raw.encode('utf-8'))
        cfg_size = tmp_fh.tell()
        tmp_fh.seek(0)
        cfg_info = tarfile.TarInfo(name=cfg_name)
        cfg_info.type = tarfile.REGTYPE
        cfg_info.size = cfg_size
        out_tarfile.addfile(cfg_info, fileobj=tmp_fh)

    # now new finally need to patch the manifest
    manifest['Config'] = cfg_name
    # wrap it in a list again
    manifest = [manifest]
    with tempfile.TemporaryFile() as fh:
        manifest_raw = json.dumps(manifest)
        fh.write(manifest_raw.encode('utf-8'))
        size = fh.tell()
        fh.seek(0)
        manifest_info = tarfile.TarInfo(name='manifest.json')
        manifest_info.type = tarfile.REGTYPE
        manifest_info.size = size
        out_tarfile.addfile(manifest_info, fh)


def _filter_single_tar(
    in_file: tarfile.TarFile,
    remove_entries,
):
    temp_fh = tempfile.TemporaryFile()
    temptar = tarfile.TarFile(fileobj=temp_fh, mode='w')

    for tar_info in in_file:
        if not tar_info.isfile():
            temptar.addfile(tar_info)
            continue

        if tar_info.name.lstrip('./') in remove_entries:
            logging.debug(f'purging entry: {tar_info.name}')
            continue

        # copy entry
        entry = in_file.extractfile(tar_info)
        temptar.addfile(tar_info, fileobj=entry)

    size = temp_fh.tell()
    temp_fh.flush()
    temp_fh.seek(0)

    return temp_fh, size
