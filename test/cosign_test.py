# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import cosign


@pytest.fixture
def image_digest_tag() -> str:
    return 'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'


@pytest.fixture
def image_reference(
    image_digest_tag: str,
) -> str:
    return f'eu.gcr.io/test/img@{image_digest_tag}'


@pytest.fixture
def payload(
    image_reference: str,
) -> bytes:
    return cosign.payload_bytes(
        image_reference=image_reference,
    )




def test_json_marshaling_with_annotations(
    image_digest_tag: str,
    image_reference: str,
):
    expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
        '"image":{"docker-manifest-digest":' \
        f'"{image_digest_tag}"' \
        '},"type":"gardener.vnd/oci/cosign-signature"},"optional":{"key":"val"}}'

    annotations = {
        "key": "val",
    }

    payload = cosign.payload_bytes(
        image_reference=image_reference,
        annotations=annotations,
    ).decode('utf-8')

    assert payload == expected_json


def test_json_marshaling_without_annotations(
    image_digest_tag: str,
    payload: bytes,
):
    expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
        '"image":{"docker-manifest-digest":' \
        f'"{image_digest_tag}"' \
        '},"type":"gardener.vnd/oci/cosign-signature"},"optional":null}'

    assert payload.decode('utf-8') == expected_json


def test_raise_error_for_img_ref_without_digest():
    img_ref = 'eu.gcr.io/test/img:1.0.0'
    with pytest.raises(ValueError):
        cosign.payload_bytes(image_reference=img_ref)
