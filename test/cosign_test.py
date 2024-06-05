# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import cosign


def test_json_marshaling_with_annotations():
    expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
        '"image":{"docker-manifest-digest":' \
        '"sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b"},' \
        '"type":"gardener.vnd/oci/cosign-signature"},"optional":{"key":"val"}}'

    image_ref = 'eu.gcr.io/test/img@' \
        'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'
    annotations = {
        "key": "val",
    }

    payload = cosign.payload_bytes(
        image_reference=image_ref,
        annotations=annotations,
    ).decode('utf-8')

    assert payload == expected_json


def test_json_marshaling_without_annotations():
    expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
        '"image":{"docker-manifest-digest":' \
        '"sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b"},' \
        '"type":"gardener.vnd/oci/cosign-signature"},"optional":null}'

    img_ref = 'eu.gcr.io/test/img@' \
        'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'

    payload = cosign.payload_bytes(
        image_reference=img_ref,
    ).decode('utf-8')

    assert payload == expected_json


def test_raise_error_for_img_ref_without_digest():
    img_ref = 'eu.gcr.io/test/img:1.0.0'
    with pytest.raises(ValueError):
        cosign.payload_bytes(image_reference=img_ref)
