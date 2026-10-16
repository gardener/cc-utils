#!/usr/bin/env python3
# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0
'''Unit tests for key-size enrichment in cbomenrich.'''
import base64
import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import sbom.cbomenrich as scbe


# ---------------------------------------------------------------------------
# Test-fixture helpers (generate keys/certs at test time — no binary blobs)
# ---------------------------------------------------------------------------

def _gen_ec_key(curve=None):
    '''Return (private_key, spki_b64, key_size).'''
    import cryptography.hazmat.primitives.asymmetric.ec as ec_mod
    import cryptography.hazmat.primitives.serialization as ser
    key = ec_mod.generate_private_key(curve or ec_mod.SECP256R1())
    pk = key.public_key()
    spki_der = pk.public_bytes(ser.Encoding.DER, ser.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki_der).decode(), pk.key_size


def _gen_rsa_key(size=2048):
    '''Return (private_key, spki_b64, key_size).'''
    import cryptography.hazmat.primitives.asymmetric.rsa as rsa_mod
    import cryptography.hazmat.primitives.serialization as ser
    key = rsa_mod.generate_private_key(65537, size)
    pk = key.public_key()
    spki_der = pk.public_bytes(ser.Encoding.DER, ser.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki_der).decode(), pk.key_size


def _cert_b64(private_key):
    '''Return base64-encoded self-signed X.509 cert DER for the given private key.'''
    import cryptography.x509 as x509
    import cryptography.hazmat.primitives.hashes as hashes
    import cryptography.hazmat.primitives.serialization as ser
    subject = issuer = x509.Name([
        x509.NameAttribute(x509.NameOID.COMMON_NAME, 'test'),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2024, 1, 1))
        .not_valid_after(datetime.datetime(2025, 1, 1))
        .sign(private_key, hashes.SHA256())
    )
    return base64.b64encode(cert.public_bytes(ser.Encoding.DER)).decode()


def _rcm_comp(value_b64, size=None):
    '''Build a minimal related-crypto-material component.'''
    rcm = {'value': value_b64}
    if size is not None:
        rcm['size'] = size
    return {
        'type': 'cryptographic-asset',
        'cryptoProperties': {
            'assetType': 'related-crypto-material',
            'relatedCryptoMaterialProperties': rcm,
        },
    }


# ---------------------------------------------------------------------------
# _compute_sizes_from_values
# ---------------------------------------------------------------------------

def test_compute_sizes_ec_spki():
    '''EC SubjectPublicKeyInfo DER → size set (existing path, no regression).'''
    _, spki_b64, expected = _gen_ec_key()
    comp = _rcm_comp(spki_b64)
    assert scbe._compute_sizes_from_values([comp]) == 1
    assert comp['cryptoProperties']['relatedCryptoMaterialProperties']['size'] == expected


def test_compute_sizes_ec_cert_der():
    '''EC key embedded in full X.509 cert DER → size set via new fallback.'''
    key, _, expected = _gen_ec_key()
    comp = _rcm_comp(_cert_b64(key))
    assert scbe._compute_sizes_from_values([comp]) == 1
    assert comp['cryptoProperties']['relatedCryptoMaterialProperties']['size'] == expected


def test_compute_sizes_rsa_cert_der():
    '''RSA key embedded in full X.509 cert DER → size set via new fallback.'''
    key, _, expected = _gen_rsa_key()
    comp = _rcm_comp(_cert_b64(key))
    assert scbe._compute_sizes_from_values([comp]) == 1
    assert comp['cryptoProperties']['relatedCryptoMaterialProperties']['size'] == expected


def test_compute_sizes_idempotent():
    '''Components with size already set are not patched again.'''
    _, spki_b64, key_size = _gen_ec_key()
    comp = _rcm_comp(spki_b64, size=key_size)
    assert scbe._compute_sizes_from_values([comp]) == 0


def test_compute_sizes_no_value():
    '''Components with no value field are skipped.'''
    comp = {
        'type': 'cryptographic-asset',
        'cryptoProperties': {
            'assetType': 'related-crypto-material',
            'relatedCryptoMaterialProperties': {},
        },
    }
    assert scbe._compute_sizes_from_values([comp]) == 0


# ---------------------------------------------------------------------------
# _propagate_key_sizes
# ---------------------------------------------------------------------------

def test_propagate_key_sizes_ec():
    '''_propagate_key_sizes sets parameterSetIdentifier for an EC algorithm component.'''
    algo_ref = 'algo-ec-1'
    algo_comp = {
        'bom-ref': algo_ref,
        'type': 'cryptographic-asset',
        'name': 'ECDSA',
        'cryptoProperties': {
            'assetType': 'algorithm',
            'algorithmProperties': {},
        },
    }
    mat_comp = {
        'type': 'cryptographic-asset',
        'name': 'EC-256',
        'cryptoProperties': {
            'assetType': 'related-crypto-material',
            'relatedCryptoMaterialProperties': {
                'size': 256,
                'algorithmRef': algo_ref,
            },
        },
    }
    assert scbe._propagate_key_sizes([algo_comp, mat_comp]) == 1
    assert algo_comp['cryptoProperties']['algorithmProperties']['parameterSetIdentifier'] == '256'


# ---------------------------------------------------------------------------
# _enrich_openssl_cnf_seclevel
# ---------------------------------------------------------------------------

def _algo_comp(name, location):
    return {
        'type': 'cryptographic-asset',
        'name': name,
        'evidence': {'occurrences': [{'location': location}]},
        'cryptoProperties': {
            'assetType': 'algorithm',
            'algorithmProperties': {},
        },
    }


def test_seclevel_dh_floor():
    '''DHE sourced only from openssl.cnf gets the SECLEVEL=2 DH floor (2048 bits).'''
    comp = _algo_comp('DHE', '/etc/ssl/openssl.cnf')
    reader = lambda path: b'CipherString = DEFAULT@SECLEVEL=2\n'
    assert scbe._enrich_openssl_cnf_seclevel([comp], reader) == 1
    ap = comp['cryptoProperties']['algorithmProperties']
    assert ap['parameterSetIdentifier'] == '2048'


def test_seclevel_ec_floor_uses_ec_table():
    '''ECDHE uses the EC floor table, not the DH one, for the same SECLEVEL.'''
    comp = _algo_comp('ECDHE', '/usr/lib/ssl/openssl.cnf')
    reader = lambda path: b'CipherString = DEFAULT@SECLEVEL=3\n'
    assert scbe._enrich_openssl_cnf_seclevel([comp], reader) == 1
    ap = comp['cryptoProperties']['algorithmProperties']
    assert ap['parameterSetIdentifier'] == '256'


def test_seclevel_lowest_wins_across_multiple_directives():
    '''Multiple SECLEVEL directives in one file -> the lowest (weakest) applies.'''
    comp = _algo_comp('DH', '/etc/ssl/openssl.cnf')
    cnf = b'CipherString = DEFAULT@SECLEVEL=3\nCipherString = ALL@SECLEVEL=1\n'
    reader = lambda path: cnf
    assert scbe._enrich_openssl_cnf_seclevel([comp], reader) == 1
    ap = comp['cryptoProperties']['algorithmProperties']
    assert ap['parameterSetIdentifier'] == '1024'


def test_seclevel_skips_non_openssl_cnf_evidence():
    '''A component with any non-openssl.cnf evidence location is left untouched.'''
    comp = _algo_comp('DHE', '/etc/ssl/openssl.cnf')
    comp['evidence']['occurrences'].append({'location': '/app/handshake.go'})
    reader = lambda path: b'CipherString = DEFAULT@SECLEVEL=2\n'
    assert scbe._enrich_openssl_cnf_seclevel([comp], reader) == 0
    ap = comp['cryptoProperties']['algorithmProperties']
    assert 'parameterSetIdentifier' not in ap


def test_seclevel_skips_already_sized():
    '''Components that already carry a key size are not overwritten.'''
    comp = _algo_comp('DHE', '/etc/ssl/openssl.cnf')
    comp['cryptoProperties']['algorithmProperties']['parameterSetIdentifier'] = '4096'
    reader = lambda path: b'CipherString = DEFAULT@SECLEVEL=2\n'
    assert scbe._enrich_openssl_cnf_seclevel([comp], reader) == 0
    ap = comp['cryptoProperties']['algorithmProperties']
    assert ap['parameterSetIdentifier'] == '4096'


def test_seclevel_no_directive_in_file():
    '''A config file with no SECLEVEL directive leaves the component unpatched.'''
    comp = _algo_comp('DHE', '/etc/ssl/openssl.cnf')
    reader = lambda path: b'MinProtocol = TLSv1.2\n'
    assert scbe._enrich_openssl_cnf_seclevel([comp], reader) == 0


def test_seclevel_unrelated_algorithm_untouched():
    '''Non-DH/EC algorithms (e.g. AES) sourced from openssl.cnf are left for other paths.'''
    comp = _algo_comp('AES128 GCM', '/usr/lib/ssl/openssl.cnf')
    reader = lambda path: b'CipherString = DEFAULT@SECLEVEL=2\n'
    assert scbe._enrich_openssl_cnf_seclevel([comp], reader) == 0
