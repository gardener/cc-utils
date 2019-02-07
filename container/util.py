# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import tarfile
import tempfile

import util


def filter_container_image(
    image_file,
    out_file,
    remove_entries,
):
    util.existing_file(image_file)
    if not remove_entries:
        raise ValueError('remove_entries must not be empty')

    with tarfile.open(image_file) as tf:
        manifest = json.load(tf.extractfile('manifest.json'))

    with tarfile.open(image_file, 'r') as in_tf, tarfile.open(out_file, 'w') as out_tf:
        _filter_files(
            manifest=manifest,
            in_tarfile=in_tf,
            out_tarfile=out_tf,
            remove_entries=set(remove_entries),
        )


def _filter_files(
    manifest,
    in_tarfile: tarfile.TarFile,
    out_tarfile: tarfile.TarFile,
    remove_entries,
):
    if not len(manifest) == 1:
        raise NotImplementedError()

    layer_paths = set(manifest[0]['Layers'])

    # copy everything that does not need to be patched
    for tar_info in in_tarfile:
        if not tar_info.isfile():
            out_tarfile.addfile(tar_info)
            continue

        fileobj = in_tarfile.extractfile(tar_info)

        if tar_info.name not in layer_paths:
            out_tarfile.addfile(tar_info, fileobj=fileobj)
            continue

        # assumption: layers are always tarfiles
        # check if we need to patch
        layer_tar = tarfile.open(fileobj=fileobj)
        have_match = bool(set(layer_tar.getnames()) & remove_entries)
        fileobj.seek(0)

        if not have_match:
            out_tarfile.addfile(tar_info, fileobj=fileobj)
        else:
            patched_tar, size = _filter_single_tar(
                in_file=layer_tar,
                remove_entries=remove_entries,
            )
            # patch tar_info to reduced size
            tar_info.size = size

            out_tarfile.addfile(tar_info, fileobj=patched_tar)
            print('patched: ' + str(tar_info.name))


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

        if tar_info.name in remove_entries:
            print(f'purging entry: {tar_info.name}')
            continue

        # copy entry
        entry = in_file.extractfile(tar_info)
        temptar.addfile(tar_info, fileobj=entry)

    size = temp_fh.tell()
    temp_fh.flush()
    temp_fh.seek(0)

    return temp_fh, size
