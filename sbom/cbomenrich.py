#!/usr/bin/env python3
'''Enrich cbomkit-theia CBOMs with key-size information not extracted during scanning.'''
import io
import json
import logging
import shutil
import subprocess
import tarfile
import uuid

logger = logging.getLogger(__name__)


def _rsa_key_size(pem_data):
    '''Parse PEM RSA public key and return bit size, or None on failure.'''
    try:
        import cryptography.hazmat.primitives.serialization as ser
        import cryptography.hazmat.primitives.asymmetric.rsa as rsa_mod
        pk = ser.load_pem_public_key(pem_data)
        if isinstance(pk, rsa_mod.RSAPublicKey):
            return pk.key_size
    except Exception:  # nosec B110
        pass
    return None


def _docker_file_reader(image_reference):
    '''
    Return (read_file, cleanup) using a temporary Docker container.

    read_file(path: str) -> bytes | None
    cleanup() -> None — removes the container

    Returns (None, no-op) if Docker is not available or container creation fails.
    '''
    if not shutil.which('docker'):
        return None, lambda: None
    try:
        container_id = subprocess.check_output(
            ['docker', 'create', '--', image_reference],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # nosec B110
        logger.debug('CBOM enrichment: docker create failed for %s', image_reference)
        return None, lambda: None

    def read_file(path):
        try:
            result = subprocess.run(
                ['docker', 'cp', f'{container_id}:{path}', '-'],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                return None
            # docker cp to stdout produces a single-file tar archive
            with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tf:
                for member in tf.getmembers():
                    if member.isfile():
                        fobj = tf.extractfile(member)
                        return fobj.read() if fobj else None
        except Exception:  # nosec B110
            pass
        return None

    def cleanup():
        subprocess.run(
            ['docker', 'rm', '--', container_id],
            capture_output=True,
            check=False,
        )

    return read_file, cleanup


def _compute_sizes_from_values(components):
    '''
    Parse the DER public key in relatedCryptoMaterialProperties.value and set size.

    cbomkit-theia embeds the base64-encoded DER SubjectPublicKeyInfo in the value field
    for keys it parses from certificates, but does not always compute the key size (RSA
    sizes are set, EC sizes are not). This fills in the gap using the embedded value.

    Returns the number of components patched.
    '''
    try:
        import base64
        import cryptography.hazmat.primitives.serialization as ser
    except ImportError:
        return 0

    patched = 0
    for comp in components:
        cp = comp.get('cryptoProperties') or {}
        if cp.get('assetType') != 'related-crypto-material':
            continue
        rcm = cp.setdefault('relatedCryptoMaterialProperties', {})
        if rcm.get('size'):
            continue
        value = rcm.get('value', '')
        if not value:
            continue
        try:
            der = base64.b64decode(value)
            pk = ser.load_der_public_key(der)
            size = pk.key_size
            if size:
                rcm['size'] = size
                patched += 1
        except Exception:  # nosec B110
            pass
    return patched


def _propagate_key_sizes(components):
    '''
    Fill algorithmProperties.parameterSetIdentifier from adjacent related-crypto-material.

    cbomkit-theia parses certificate public keys and records their sizes in
    relatedCryptoMaterialProperties.size, but leaves the referenced algorithm
    component's parameterSetIdentifier empty.  Walk the graph and propagate.

    Returns the number of algorithm components patched.
    '''
    by_ref = {c.get('bom-ref'): c for c in components}
    patched = 0
    for comp in components:
        cp = comp.get('cryptoProperties') or {}
        if cp.get('assetType') != 'related-crypto-material':
            continue
        rcm = cp.get('relatedCryptoMaterialProperties') or {}
        size = rcm.get('size')
        algo_ref = rcm.get('algorithmRef')
        if not size or not algo_ref:
            continue
        algo = by_ref.get(algo_ref)
        if not algo:
            continue
        algo_cp = algo.setdefault('cryptoProperties', {})
        algo_ap = algo_cp.setdefault('algorithmProperties', {})
        if not algo_ap.get('parameterSetIdentifier'):
            algo_ap['parameterSetIdentifier'] = str(size)
            patched += 1
    return patched


def _enrich_apk_keys(components, file_reader):
    '''
    Inject algorithm + related-crypto-material components for APK signing keys.

    cbomkit-theia records .rsa.pub files as bare file components (hashes only).
    For each such file that has not already been enriched, extract the PEM from
    the image, parse the RSA key size, and append two new CBOM components.

    Returns a list of new components to append.
    '''
    # Paths that already have a related-crypto-material entry — skip them
    enriched_locations = set()
    for comp in components:
        cp = comp.get('cryptoProperties') or {}
        if cp.get('assetType') == 'related-crypto-material':
            for occ in (comp.get('evidence') or {}).get('occurrences') or []:
                enriched_locations.add(occ.get('location', ''))

    new_comps = []
    for comp in components:
        if comp.get('type') != 'file':
            continue
        path = comp.get('name', '')
        if not path.endswith('.rsa.pub'):
            continue
        if path in enriched_locations:
            continue
        pem_data = file_reader(path)
        if not pem_data:
            continue
        size = _rsa_key_size(pem_data)
        if not size:
            continue
        algo_ref = str(uuid.uuid4())
        mat_ref = str(uuid.uuid4())
        occ = {'occurrences': [{'location': path}]}
        new_comps += [
            {
                'bom-ref': algo_ref,
                'type': 'cryptographic-asset',
                'name': 'RSA',
                'evidence': occ,
                'cryptoProperties': {
                    'assetType': 'algorithm',
                    'algorithmProperties': {
                        'primitive': 'signature',
                        'cryptoFunctions': ['sign', 'verify'],
                        'parameterSetIdentifier': str(size),
                    },
                    'oid': '1.2.840.113549.1.1.1',
                },
            },
            {
                'bom-ref': mat_ref,
                'type': 'cryptographic-asset',
                'name': f'RSA-{size}',
                'evidence': occ,
                'cryptoProperties': {
                    'assetType': 'related-crypto-material',
                    'relatedCryptoMaterialProperties': {
                        'type': 'public-key',
                        'algorithmRef': algo_ref,
                        'size': size,
                        'format': 'PEM',
                    },
                    'oid': '1.2.840.113549.1.1.1',
                },
            },
        ]
        logger.debug('CBOM enrichment: APK key %s -> RSA-%d', path, size)
    return new_comps


def enrich(cbom_bytes, image_reference=None):
    '''
    Return enriched CBOM bytes.

    Enrichment applied:
    1. Propagate key sizes from related-crypto-material components to their referenced
       algorithm components' parameterSetIdentifier (works without image access).
    2. If image_reference is given and Docker is available, inject RSA algorithm and
       key-material components for APK signing keys (.rsa.pub files).
    '''
    doc = json.loads(cbom_bytes)
    components = doc.get('components') or []

    sized = _compute_sizes_from_values(components)
    if sized:
        logger.info(
            'CBOM enrichment: computed size for %d key-material component(s) from DER value',
            sized,
        )

    patched = _propagate_key_sizes(components)
    logger.info('CBOM enrichment: set parameterSetIdentifier on %d algorithm(s)', patched)

    if image_reference:
        file_reader, cleanup = _docker_file_reader(image_reference)
        try:
            if file_reader:
                new_comps = _enrich_apk_keys(components, file_reader)
                if new_comps:
                    components.extend(new_comps)
                    doc['components'] = components
                    logger.info(
                        'CBOM enrichment: injected %d component(s) for APK signing keys',
                        len(new_comps),
                    )
            else:
                logger.debug('CBOM enrichment: Docker unavailable, skipping APK key enrichment')
        finally:
            cleanup()

    return json.dumps(doc, ensure_ascii=False).encode()
