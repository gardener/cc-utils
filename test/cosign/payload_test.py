# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import cosign.payload as cp


def test_json_marshaling_with_annotations():
    expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
        '"image":{"docker-manifest-digest":' \
        '"sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b"},' \
        '"type":"gardener.vnd/oci/cosign-signature"},"optional":{"key":"val"}}'

    img_ref = 'eu.gcr.io/test/img@' \
        'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'
    annotations = {
        "key": "val",
    }

    payload = cp.Payload(
        image_ref=img_ref,
        annotations=annotations,
    )

    actual_json = payload.normalised_json()

    assert actual_json == expected_json


def test_json_marshaling_without_annotations():
    expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
        '"image":{"docker-manifest-digest":' \
        '"sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b"},' \
        '"type":"gardener.vnd/oci/cosign-signature"},"optional":null}'

    img_ref = 'eu.gcr.io/test/img@' \
        'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'

    payload = cp.Payload(
        image_ref=img_ref,
    )

    actual_json = payload.normalised_json()

    assert actual_json == expected_json


def test_raise_error_for_img_ref_without_digest():
    img_ref = 'eu.gcr.io/test/img:1.0.0'
    with pytest.raises(ValueError):
        cp.Payload(image_ref=img_ref)
