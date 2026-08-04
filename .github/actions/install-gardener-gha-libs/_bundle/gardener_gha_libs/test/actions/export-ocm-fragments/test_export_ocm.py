# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import tempfile

# test/__init__.py already adds repo_root to sys.path;
# add the action directory so export_ocm is importable
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'export-ocm-fragments')
    ),
)

import pytest

import export_ocm


def _make_blobs_dir(files: dict[str, bytes]) -> str:
    '''creates a temp blobs dir with given filename->content mapping'''
    blobs_dir = tempfile.mkdtemp()
    for name, content in files.items():
        with open(os.path.join(blobs_dir, name), 'wb') as f:
            f.write(content)
    return blobs_dir


def test_content_address_blobs_renames_and_symlinks():
    content = b'hello blob'
    blobs_dir = _make_blobs_dir({'myblob.bin': content})

    result = export_ocm.content_address_blobs(blobs_dir)

    assert len(result) == 2  # fname and blobs_dir/fname entries
    digest_name = result['myblob.bin']
    assert digest_name.startswith('sha256:')

    # digest-named file exists
    assert os.path.isfile(os.path.join(blobs_dir, digest_name))
    # original name is now a symlink pointing to digest name
    orig_path = os.path.join(blobs_dir, 'myblob.bin')
    assert os.path.islink(orig_path)
    assert os.readlink(orig_path) == digest_name


def test_content_address_blobs_already_digest_named():
    import hashlib
    content = b'already addressed'
    digest = 'sha256:' + hashlib.sha256(content).hexdigest()
    blobs_dir = _make_blobs_dir({digest: content})

    result = export_ocm.content_address_blobs(blobs_dir)

    # no renaming needed — result mapping is empty
    assert result == {}
    assert os.path.isfile(os.path.join(blobs_dir, digest))


def test_patch_local_blob_refs_rewrites_localreference():
    content = b'blob content'
    blobs_dir = _make_blobs_dir({'myblob.bin': content})
    orig_to_digest = export_ocm.content_address_blobs(blobs_dir)

    artefact = {
        'name': 'my-resource',
        'access': {
            'type': 'localBlob',
            'localReference': 'myblob.bin',
        },
    }

    export_ocm.patch_local_blob_refs(
        artefacts=[artefact],
        orig_to_digest=orig_to_digest,
        blobs_dir=blobs_dir,
    )

    assert artefact['access']['localReference'].startswith('sha256:')
    assert 'size' in artefact['access']


def test_patch_local_blob_refs_missing_blob_raises():
    blobs_dir = tempfile.mkdtemp()  # empty

    artefact = {
        'name': 'my-resource',
        'access': {
            'type': 'localBlob',
            'localReference': 'nonexistent.bin',
        },
    }

    with pytest.raises(ValueError, match='nonexistent.bin'):
        export_ocm.patch_local_blob_refs(
            artefacts=[artefact],
            orig_to_digest={},
            blobs_dir=blobs_dir,
        )
