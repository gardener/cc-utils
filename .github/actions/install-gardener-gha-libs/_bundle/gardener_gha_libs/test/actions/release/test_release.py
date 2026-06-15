# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import hashlib
import os
import sys
import unittest.mock as mock

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions', 'release')
    ),
)

import release
import release_notes.ocm as rn_ocm


def _make_component(version='1.0.0'):
    comp = mock.MagicMock()
    comp.version = version
    comp.resources = []
    comp.current_ocm_repo.component_version_oci_ref.return_value = 'registry.example.com/comp:1.0.0'
    return comp


# --- attach_release_notes ---

def test_markdown_blob_uploaded_when_non_empty():
    component = _make_component()
    oci_client = mock.MagicMock()
    markdown = 'some release notes'

    release.attach_release_notes(component, markdown, b'tar', oci_client)

    octets = markdown.encode('utf-8')
    expected_digest = f'sha256:{hashlib.sha256(octets).hexdigest()}'
    oci_client.put_blob.assert_any_call(
        image_reference='registry.example.com/comp:1.0.0',
        digest=expected_digest,
        octets_count=len(octets),
        data=octets,
    )


def test_markdown_blob_skipped_when_empty():
    component = _make_component()
    oci_client = mock.MagicMock()

    release.attach_release_notes(component, '', b'tar', oci_client)

    # only the tar blob should be uploaded
    assert oci_client.put_blob.call_count == 1


def test_tar_blob_always_uploaded():
    component = _make_component()
    oci_client = mock.MagicMock()
    tar_bytes = b'tar data'

    release.attach_release_notes(component, '', tar_bytes, oci_client)

    expected_digest = f'sha256:{hashlib.sha256(tar_bytes).hexdigest()}'
    oci_client.put_blob.assert_called_once_with(
        image_reference='registry.example.com/comp:1.0.0',
        digest=expected_digest,
        octets_count=len(tar_bytes),
        data=tar_bytes,
    )


def test_release_notes_resources_appended_with_markdown():
    component = _make_component()
    oci_client = mock.MagicMock()

    release.attach_release_notes(component, 'notes', b'tar', oci_client)

    names = [r.name for r in component.resources]
    assert rn_ocm.release_notes_resource_name_old in names
    assert rn_ocm.release_notes_resource_name in names


def test_release_notes_resources_appended_without_markdown():
    component = _make_component()
    oci_client = mock.MagicMock()

    release.attach_release_notes(component, '', b'tar', oci_client)

    names = [r.name for r in component.resources]
    assert rn_ocm.release_notes_resource_name_old not in names
    assert rn_ocm.release_notes_resource_name in names


# --- attach_branch_info ---

def test_branch_info_blob_uploaded():
    component = _make_component()
    oci_client = mock.MagicMock()
    data = b'branch: main\n'

    release.attach_branch_info(component, data, oci_client)

    expected_digest = f'sha256:{hashlib.sha256(data).hexdigest()}'
    oci_client.put_blob.assert_called_once_with(
        image_reference=mock.ANY,
        digest=expected_digest,
        octets_count=len(data),
        data=data,
    )


def test_branch_info_resource_appended():
    component = _make_component()
    oci_client = mock.MagicMock()

    release.attach_branch_info(component, b'data', oci_client)

    assert len(component.resources) == 1
    resource = component.resources[0]
    assert resource.name == 'branch-info'
    assert resource.version == '1.0.0'
