# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import hashlib
import json

import cryptography.hazmat.primitives.asymmetric.rsa as rsa
import cryptography.hazmat.primitives.serialization as crypto_serialiation
import pytest

import cosign
import model.signing_server
import oci.model


@pytest.fixture
def image_digest_tag() -> str:
    return 'sha256:a904c847d39ae82ec8859ce623ae14ccbfff36d53ce1490b43d9bf5caa47f33b'


@pytest.fixture
def image_reference(
    image_digest_tag: str,
) -> str:
    return f'eu.gcr.io/test/img@{image_digest_tag}'


@pytest.fixture
def payload_annotations() -> dict[str, str]:
    return {
        'key': 'val',
    }


@pytest.fixture
def payload_without_annotations(
    image_reference: str,
) -> bytes:
    return cosign.payload_bytes(
        image_reference=image_reference,
    )


@pytest.fixture
def payload_without_annotations_size(
    payload_without_annotations: bytes,
) -> int:
    return len(payload_without_annotations)


@pytest.fixture
def payload_without_annotations_digest(
    payload_without_annotations: bytes,
) -> str:
    return f'sha256:{hashlib.sha256(payload_without_annotations).hexdigest()}'


@pytest.fixture
def payload_with_annotations(
    image_reference: str,
    payload_annotations: dict[str, str],
) -> bytes:
    return cosign.payload_bytes(
        image_reference=image_reference,
        annotations=payload_annotations,
        overwrite_docker_reference=None,
    )


@pytest.fixture
def payload_with_annotations_size(
    payload_with_annotations: bytes,
) -> int:
    return len(payload_with_annotations)


@pytest.fixture
def payload_with_annotations_digest(
    payload_with_annotations: bytes,
) -> str:
    return f'sha256:{hashlib.sha256(payload_with_annotations).hexdigest()}'


@pytest.fixture
def cfg_blob(
    payload_without_annotations_digest: str,
) -> bytes:
    return cosign.cfg_blob_bytes(
        payload_digest=payload_without_annotations_digest,
    )


@pytest.fixture
def cfg_blob_size(
    cfg_blob: bytes,
) -> int:
    return len(cfg_blob)


@pytest.fixture
def cfg_blob_digest(
    cfg_blob: bytes,
) -> str:
    return f'sha256:{hashlib.sha256(cfg_blob).hexdigest()}'


def generate_public_key() -> str:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )
    return private_key.public_key().public_bytes(
        encoding=crypto_serialiation.Encoding.PEM,
        format=crypto_serialiation.PublicFormat.SubjectPublicKeyInfo,
    ).decode('utf-8')


@pytest.fixture
def public_key_1() -> str:
    return generate_public_key()


@pytest.fixture
def public_key_2() -> str:
    return generate_public_key()


def test_json_marshaling_with_annotations(
    image_digest_tag: str,
    payload_with_annotations: bytes,
):
    expected_json = '{"critical":{"identity":{"docker-reference":null},' \
        '"image":{"docker-manifest-digest":' \
        f'"{image_digest_tag}"' \
        '},"type":"gardener.vnd/oci/cosign-signature"},"optional":{"key":"val"}}'

    assert payload_with_annotations.decode('utf-8') == expected_json


def test_json_marshaling_without_annotations(
    image_digest_tag: str,
    payload_without_annotations: bytes,
):
    expected_json = '{"critical":{"identity":{"docker-reference":"eu.gcr.io/test/img"},' \
        '"image":{"docker-manifest-digest":' \
        f'"{image_digest_tag}"' \
        '},"type":"gardener.vnd/oci/cosign-signature"},"optional":null}'

    assert payload_without_annotations.decode('utf-8') == expected_json


def test_raise_error_for_img_ref_without_digest():
    img_ref = 'eu.gcr.io/test/img:1.0.0'
    with pytest.raises(ValueError):
        cosign.payload_bytes(image_reference=img_ref)


def test_image_signing(
    payload_with_annotations_size: int,
    payload_with_annotations_digest: str,
    payload_without_annotations_size: int,
    payload_without_annotations_digest: str,
    cfg_blob_size: int,
    cfg_blob_digest: str,
    public_key_1: str,
    public_key_2: str,
):
    '''
    Tests signing of an imaginary image multiple times in a row, starting without an existing
    signature manifest and re-using the created manifest for subsequent signatures (append mode).

    Scenarios:
    1. Signing using `rsassa-pss` algorithm and `public_key_1`
    2. Re-signing using same algorithm and key but different signature (as algorithm enforces)
    3. Re-signing using `rsassa-pkcs1-v1-5` algorithm and `public_key_1`
    4. Re-signing using `rsassa-pss` algorithm again but with `public_key_2` and changed signature
    5. Re-signing using `rsassa-pss` algorithm and `public_key_2` again but with changed payload
    '''
    manifest = oci.model.OciImageManifest(
        config=oci.model.OciBlobRef(
            digest=cfg_blob_digest,
            mediaType='application/vnd.oci.image.config.v1+json',
            size=cfg_blob_size,
        ),
        mediaType='application/vnd.oci.image.manifest.v1+json',
        layers=[],
        annotations={},
    )

    signature = 'rsassa-pss-signature-dummy'
    signing_algorithm = model.signing_server.SigningAlgorithm.RSASSA_PSS

    signed_manifest = cosign.sign_manifest(
        manifest=manifest,
        payload_size=payload_with_annotations_size,
        payload_digest=payload_with_annotations_digest,
        signature=signature,
        signing_algorithm=signing_algorithm,
        public_key=public_key_1,
        on_exist=cosign.OnExist.APPEND,
    )

    assert len(signed_manifest.layers) == 1
    signature_layer = signed_manifest.layers[0]
    assert signature_layer.annotations.get(cosign._cosign_signature_annotation_name) == signature
    assert signature_layer.size == payload_with_annotations_size
    assert signature_layer.digest == payload_with_annotations_digest

    signature = 'rsassa-pss-signature-dummy-changed'

    manifest_resigned = cosign.sign_manifest(
        manifest=dataclasses.replace(signed_manifest), # copy of previous manifest
        payload_size=payload_with_annotations_size,
        payload_digest=payload_with_annotations_digest,
        signature=signature,
        signing_algorithm=signing_algorithm,
        public_key=public_key_1,
        on_exist=cosign.OnExist.APPEND,
    )

    assert signed_manifest == manifest_resigned # nothing should have changed during re-signing

    signature = 'rsassa-pkcs1-v1-5-signature-dummy'
    signing_algorithm = model.signing_server.SigningAlgorithm.RSASSA_PKCS1_V1_5

    manifest_different_algorithm = cosign.sign_manifest(
        manifest=dataclasses.replace(manifest_resigned), # copy of previous manifest
        payload_size=payload_with_annotations_size,
        payload_digest=payload_with_annotations_digest,
        signature=signature,
        signing_algorithm=signing_algorithm,
        public_key=public_key_1,
        on_exist=cosign.OnExist.APPEND,
    )

    assert len(manifest_different_algorithm.layers) == 2
    signature_layer = manifest_different_algorithm.layers[1]
    assert signature_layer.annotations.get(cosign._cosign_signature_annotation_name) == signature
    assert signature_layer.size == payload_with_annotations_size
    assert signature_layer.digest == payload_with_annotations_digest

    signature = 'rsassa-pss-signature-dummy-changed-again'
    signing_algorithm = model.signing_server.SigningAlgorithm.RSASSA_PSS

    manifest_different_key = cosign.sign_manifest(
        manifest=dataclasses.replace(manifest_different_algorithm), # copy of previous manifest
        payload_size=payload_with_annotations_size,
        payload_digest=payload_with_annotations_digest,
        signature=signature,
        signing_algorithm=signing_algorithm,
        public_key=public_key_2,
        on_exist=cosign.OnExist.APPEND,
    )

    assert len(manifest_different_key.layers) == 3
    signature_layer = manifest_different_key.layers[2]
    assert signature_layer.annotations.get(cosign._cosign_signature_annotation_name) == signature
    assert signature_layer.size == payload_with_annotations_size
    assert signature_layer.digest == payload_with_annotations_digest

    signature = 'rsassa-pss-signature-dummy-changed-payload'
    signing_algorithm = model.signing_server.SigningAlgorithm.RSASSA_PSS

    manifest_different_key = cosign.sign_manifest(
        manifest=dataclasses.replace(manifest_different_algorithm), # copy of previous manifest
        payload_size=payload_without_annotations_size,
        payload_digest=payload_without_annotations_digest,
        signature=signature,
        signing_algorithm=signing_algorithm,
        public_key=public_key_2,
        on_exist=cosign.OnExist.APPEND,
    )

    assert len(manifest_different_key.layers) == 4
    signature_layer = manifest_different_key.layers[3]
    assert signature_layer.annotations.get(cosign._cosign_signature_annotation_name) == signature
    assert signature_layer.size == payload_without_annotations_size
    assert signature_layer.digest == payload_without_annotations_digest

    json.dumps(manifest_different_key.as_dict()).encode('utf-8')
