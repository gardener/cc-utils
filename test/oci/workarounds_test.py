import unittest.mock

import pytest
import requests

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
