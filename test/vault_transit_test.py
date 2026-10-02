# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0


import base64
import hashlib
import unittest.mock

import pytest

import signingserver
import vault_transit


def test_signing_response_raw_envelope_with_certificate_chain():
    # when a cert chain is attached, `.raw` must match the signing-server structure: the
    # CERTIFICATE block(s) precede the SIGNATURE block, so both extract identically.
    certificate_chain = (
        '-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n'
        '-----BEGIN CERTIFICATE-----\nintermediate\n-----END CERTIFICATE-----\n'
    )
    response = vault_transit.VaultTransitSigningResponse(
        signature='dGhlLXNpZ25hdHVyZQ==',
        public_key='-----BEGIN PUBLIC KEY-----\nMFoo\n-----END PUBLIC KEY-----\n',
        public_key_version='1',
        signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PSS,
        certificate_chain=certificate_chain,
    )

    raw = response.raw
    # certificate must come first, then the signature (matching signing-server layout)
    assert raw.startswith('-----BEGIN CERTIFICATE-----')
    assert raw.index('-----BEGIN CERTIFICATE-----') < raw.index('-----BEGIN SIGNATURE-----')
    assert raw.strip().endswith('-----END SIGNATURE-----')

    reparsed = signingserver.SigningResponse(
        raw=raw,
        signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PSS,
    )
    # both the (first/leaf) certificate and the signature must round-trip via signingserver's parser
    assert reparsed.signature == response.signature
    assert '-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----' in reparsed.certificate


def _client_with_mocked_hvac(sign_response, key='my-key', engine='transit', certificate_chains=None):
    """
    Builds a VaultTransitClient whose underlying hvac client is replaced by a mock returning
    `sign_response` for `secrets.transit.sign_data`. The client is constructed through its real
    `__init__` with `hvac.Client` patched out, so no network/hvac dependency is required.

    `certificate_chains` optionally maps a key version (str) to a PEM certificate-chain string, which
    is added to that version's entry in the mocked `read_key` response (as Vault does when a chain
    was attached via `set-certificate-chain`).
    """
    certificate_chains = certificate_chains or {}
    cfg = vault_transit.VaultTransitClientCfg(
        base_url='https://vault.example.invalid',
        token='dummy-token',
        engine=engine,
        key=key,
    )
    keys = {
        '1': {'public_key': '-----BEGIN PUBLIC KEY-----\nold\n-----END PUBLIC KEY-----\n'},
        '2': {'public_key': '-----BEGIN PUBLIC KEY-----\nnew\n-----END PUBLIC KEY-----\n'},
    }
    for version, chain in certificate_chains.items():
        keys[version]['certificate_chain'] = chain

    hvac_client = unittest.mock.MagicMock()
    hvac_client.secrets.transit.sign_data.return_value = sign_response
    hvac_client.secrets.transit.read_key.return_value = {
        'data': {
            'latest_version': 2,
            'keys': keys,
        },
    }
    with unittest.mock.patch('vault_transit.hvac.Client', return_value=hvac_client):
        client = vault_transit.VaultTransitClient(cfg)

    return client, hvac_client


def test_sign_strips_vault_prefix_and_forwards_expected_args():
    digest = bytes.fromhex('ab' * 32) # 32-byte sha256 digest
    signed_b64 = base64.b64encode(b'the-signature').decode('utf-8')
    v2_chain = '-----BEGIN CERTIFICATE-----\nv2-leaf\n-----END CERTIFICATE-----\n'
    client, hvac_client = _client_with_mocked_hvac(
        {'data': {'signature': f'vault:v2:{signed_b64}'}},
        certificate_chains={'2': v2_chain}, # chain required for sign() to succeed
    )

    response = client.sign(
        digest=digest,
        hash_algorithm='sha256',
        signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PSS,
    )

    # `vault:v<n>:` prefix must be stripped -> raw base64
    assert response.signature == signed_b64
    assert response.signing_algorithm is signingserver.SigningAlgorithm.RSASSA_PSS
    # public key is fetched for the version that signed (v2 here)
    assert 'new' in response.public_key
    assert response.public_key_version == '2'
    # the chain attached to the signing version is carried on the response
    assert response.certificate_chain == v2_chain

    _, kwargs = hvac_client.secrets.transit.sign_data.call_args
    assert kwargs['name'] == 'my-key'
    assert kwargs['mount_point'] == 'transit'
    assert kwargs['prehashed'] is True
    assert kwargs['hash_algorithm'] == 'sha2-256'
    assert kwargs['signature_algorithm'] == 'pss'
    assert kwargs['salt_length'] == 'auto' # PSS.MAX_LENGTH, to match the OCM verifier
    # digest is passed base64-encoded
    assert base64.b64decode(kwargs['hash_input']) == digest


def test_sign_includes_certificate_chain_of_signing_version():
    signed_b64 = base64.b64encode(b'the-signature').decode('utf-8')
    v2_chain = '-----BEGIN CERTIFICATE-----\nv2-leaf\n-----END CERTIFICATE-----\n'
    v1_chain = '-----BEGIN CERTIFICATE-----\nv1-leaf\n-----END CERTIFICATE-----\n'
    # signature is made by v2; both versions have a (different) chain attached
    client, _ = _client_with_mocked_hvac(
        {'data': {'signature': f'vault:v2:{signed_b64}'}},
        certificate_chains={'1': v1_chain, '2': v2_chain},
    )

    response = client.sign(
        digest=bytes.fromhex('ab' * 32),
        signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PSS,
    )

    # the chain of the SIGNING version (v2) must be used, not latest-by-coincidence or v1
    assert response.certificate_chain == v2_chain
    assert 'v2-leaf' in response.raw
    assert 'v1-leaf' not in response.raw
    # and it round-trips through the signing-server parser
    reparsed = signingserver.SigningResponse(
        raw=response.raw,
        signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PSS,
    )
    assert reparsed.signature == response.signature
    assert 'v2-leaf' in reparsed.certificate


def test_sign_requires_certificate_chain_on_signing_version():
    # the vault-transit backend MUST be a complete drop-in for the signing-server: a signature is
    # only emitted when the signing key version carries a certificate chain. Without one, `sign()`
    # should fail instead of producing a cert-less envelope.
    signed_b64 = base64.b64encode(b'the-signature').decode('utf-8')
    client, _ = _client_with_mocked_hvac(
        {'data': {'signature': f'vault:v2:{signed_b64}'}},
    ) # no certificate_chains -> key versions carry no chain

    with pytest.raises(vault_transit.VaultTransitException):
        client.sign(
            digest=bytes.fromhex('ab' * 32),
            signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PSS,
        )


def test_sign_rejects_empty_or_whitespace_certificate_chain():
    # a present-but-blank chain from Vault (empty or whitespace-only) must be treated as "no chain".
    signed_b64 = base64.b64encode(b'the-signature').decode('utf-8')
    for blank in ('', '   \n\t '):
        client, _ = _client_with_mocked_hvac(
            {'data': {'signature': f'vault:v2:{signed_b64}'}},
            certificate_chains={'2': blank},
        )

        with pytest.raises(vault_transit.VaultTransitException):
            client.sign(
                digest=bytes.fromhex('ab' * 32),
                signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PSS,
            )


def test_sign_pkcs1v15_has_no_salt_length():
    signed_b64 = base64.b64encode(b'sig').decode('utf-8')
    chain = '-----BEGIN CERTIFICATE-----\nv1-leaf\n-----END CERTIFICATE-----\n'
    client, hvac_client = _client_with_mocked_hvac(
        {'data': {'signature': f'vault:v1:{signed_b64}'}},
        certificate_chains={'1': chain}, # chain required for sign() to succeed
    )

    client.sign(
        digest=bytes.fromhex('cd' * 32),
        signing_algorithm=signingserver.SigningAlgorithm.RSASSA_PKCS1_V1_5,
    )

    _, kwargs = hvac_client.secrets.transit.sign_data.call_args
    assert kwargs['signature_algorithm'] == 'pkcs1v15'
    assert kwargs['salt_length'] is None


def test_sign_hashes_content_locally():
    content = b'hello-world'
    expected_digest = hashlib.sha256(content).digest()
    signed_b64 = base64.b64encode(b'sig').decode('utf-8')
    chain = '-----BEGIN CERTIFICATE-----\nv1-leaf\n-----END CERTIFICATE-----\n'
    client, hvac_client = _client_with_mocked_hvac(
        {'data': {'signature': f'vault:v1:{signed_b64}'}},
        certificate_chains={'1': chain}, # chain required for sign() to succeed
    )

    client.sign(content=content, hash_algorithm='sha256')

    _, kwargs = hvac_client.secrets.transit.sign_data.call_args
    # content must be hashed locally (prehashed=True), matching signingserver behaviour
    assert base64.b64decode(kwargs['hash_input']) == expected_digest


def test_sign_requires_exactly_one_of_content_or_digest():
    client, _ = _client_with_mocked_hvac({'data': {'signature': 'vault:v1:x'}})

    with pytest.raises(ValueError):
        client.sign() # neither

    with pytest.raises(ValueError):
        client.sign(content=b'x', digest=bytes.fromhex('ef' * 32)) # both


def test_sign_rejects_unsupported_hash_algorithm():
    client, _ = _client_with_mocked_hvac({'data': {'signature': 'vault:v1:x'}})

    with pytest.raises(ValueError):
        client.sign(digest=bytes.fromhex('ef' * 32), hash_algorithm='md5')


def test_public_key_without_public_key_raises():
    client, hvac_client = _client_with_mocked_hvac({'data': {'signature': 'vault:v1:x'}})
    # symmetric key -> no public_key field
    hvac_client.secrets.transit.read_key.return_value = {
        'data': {'latest_version': 1, 'keys': {'1': {}}},
    }

    with pytest.raises(vault_transit.VaultTransitException):
        client.public_key_version('1')
