import hashlib
import unittest.mock

import pytest
import requests

import oci.model as om
import oci.workarounds as ow


def _mock_response(status_code):
    r = unittest.mock.Mock(spec=requests.Response)
    r.status_code = status_code
    r.ok = (status_code < 400)
    return r


def test_patch_head_blob_to_use_get_present():
    oci_client = unittest.mock.Mock()
    oci_client.blob.return_value = _mock_response(200)

    ow.patch_head_blob_to_use_get(oci_client)
    res = oci_client.head_blob(image_reference='example.com/foo:bar', digest='sha256:abc')

    assert res.ok
    assert res.status_code == 200
    oci_client.blob.assert_called_once_with(
        image_reference='example.com/foo:bar',
        digest='sha256:abc',
        stream=True,
        absent_ok=False,
        warn_if_not_ok=False,
    )
    res.close.assert_called_once()


def test_patch_head_blob_to_use_get_absent_ok():
    oci_client = unittest.mock.Mock()
    response = _mock_response(404)
    oci_client.blob.side_effect = requests.exceptions.HTTPError(response=response)

    ow.patch_head_blob_to_use_get(oci_client)
    res = oci_client.head_blob(
        image_reference='example.com/foo:bar',
        digest='sha256:abc',
        absent_ok=True,
    )

    assert not res.ok
    assert res.status_code == 404
    response.close.assert_called_once()


def test_patch_head_blob_to_use_get_absent_not_ok_raises():
    oci_client = unittest.mock.Mock()
    response = _mock_response(404)
    oci_client.blob.side_effect = requests.exceptions.HTTPError(response=response)

    ow.patch_head_blob_to_use_get(oci_client)
    with pytest.raises(requests.exceptions.HTTPError):
        oci_client.head_blob(
            image_reference='example.com/foo:bar',
            digest='sha256:abc',
            absent_ok=False,
        )


def test_patch_head_blob_to_use_get_other_error_propagates():
    oci_client = unittest.mock.Mock()
    response = _mock_response(500)
    oci_client.blob.side_effect = requests.exceptions.HTTPError(response=response)

    ow.patch_head_blob_to_use_get(oci_client)
    with pytest.raises(requests.exceptions.HTTPError):
        oci_client.head_blob(
            image_reference='example.com/foo:bar',
            digest='sha256:abc',
            absent_ok=True,
        )


def test_patch_head_blob_to_ignore_existing_blobs_always_reports_absent():
    oci_client = unittest.mock.Mock()
    oci_client.blob.return_value = _mock_response(200) # would-be-present, must be ignored

    ow.patch_head_blob_to_ignore_existing_blobs(oci_client)
    res = oci_client.head_blob(image_reference='example.com/foo:bar', digest='sha256:abc')

    assert not res.ok
    assert res.status_code == 404
    oci_client.blob.assert_not_called()


def _make_client_silently_dropping_manifests():
    '''
    returns a mocked oci-client whose manifest-PUT accepts (HTTP 20x) but whose GET yields 404,
    mimicking the observed Artifactory bug.
    '''

    class _SilentDropClient:
        def put_manifest(self, image_reference, manifest, *args, **kwargs):
            return _mock_response(201)

        def manifest_raw(self, image_reference, *args, **kwargs):
            raise requests.exceptions.HTTPError(response=_mock_response(404))

    return _SilentDropClient()


def test_patch_put_manifest_to_validate_via_get_dropped_manifest_raises():
    oci_client = _make_client_silently_dropping_manifests()

    ow.patch_put_manifest_to_validate_via_get(oci_client)

    with pytest.raises(requests.exceptions.HTTPError):
        oci_client.put_manifest(
            image_reference='example.com/foo:bar',
            manifest=b'{"schemaVersion": 2}',
        )


def test_patch_put_manifest_to_validate_via_get_served_manifest_passes():
    class _ServingClient:
        def __init__(self):
            self.validated_refs = []

        def put_manifest(self, image_reference, manifest, *args, **kwargs):
            self.manifest = manifest
            return _mock_response(201)

        def manifest_raw(self, image_reference, *args, **kwargs):
            self.validated_refs.append(image_reference)
            return _mock_response(200)

    oci_client = _ServingClient()
    ow.patch_put_manifest_to_validate_via_get(oci_client)

    manifest = b'{"schemaVersion": 2}'
    res = oci_client.put_manifest(image_reference='example.com/foo:bar', manifest=manifest)

    assert res.ok
    expected_digest = 'sha256:' + hashlib.sha256(manifest).hexdigest()
    assert oci_client.validated_refs == [f'example.com/foo@{expected_digest}']


def test_patch_put_manifest_to_validate_via_get_validates_digest_ref_verbatim():
    class _ServingClient:
        def __init__(self):
            self.validated_refs = []

        def put_manifest(self, image_reference, manifest, *args, **kwargs):
            return _mock_response(201)

        def manifest_raw(self, image_reference, *args, **kwargs):
            self.validated_refs.append((image_reference, kwargs))
            return _mock_response(200)

    oci_client = _ServingClient()
    ow.patch_put_manifest_to_validate_via_get(oci_client)

    digest_ref = (
        'example.com/foo@sha256:'
        '0000000000000000000000000000000000000000000000000000000000000000'
    )
    oci_client.put_manifest(image_reference=digest_ref, manifest=b'{"schemaVersion": 2}')

    assert oci_client.validated_refs == [
        (digest_ref, {'accept': om.MimeTypes.multiarch, 'absent_ok': False}),
    ]
