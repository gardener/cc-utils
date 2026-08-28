#!/usr/bin/env python3
'''Enrich cbomkit-theia CBOMs with key-size information not extracted during scanning.'''
import gzip
import io
import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
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
        return None

    def cleanup():
        subprocess.run(
            ['docker', 'rm', '--', container_id],
            capture_output=True,
            check=False,
        )

    return read_file, cleanup


def _oci_file_reader(image_reference, oci_client):
    '''Return a read_file callable backed by OCI layer downloads, or None on failure.'''
    try:
        import oci.model as om
        ref = om.OciImageReference.to_image_ref(image_reference)
        repo_ref = ref.ref_without_tag
        manifest = oci_client.manifest(image_reference)
        if isinstance(manifest, om.OciImageManifestList):
            entries = [
                e for e in manifest.manifests
                if e.platform and e.platform.os == 'linux'
                and e.platform.architecture == 'amd64'
            ]
            entry = entries[0] if entries else (
                manifest.manifests[0] if manifest.manifests else None
            )
            if entry is None:
                return None
            manifest = oci_client.manifest(f'{repo_ref}@{entry.digest}')
        if manifest is None:
            return None
    except Exception as exc:
        logger.debug('CBOM enrichment: OCI manifest fetch failed for %s: %s', image_reference, exc)
        raise

    # newest layer first — read_file returns the topmost (container-visible) version of a file
    layers = list(reversed(manifest.layers))
    layer_cache = {}  # digest -> TemporaryFile (seekable, decompressed)

    def _load_layer(layer):
        digest = layer.digest
        if digest in layer_cache:
            return layer_cache[digest]
        resp = oci_client.blob(
            image_reference=repo_ref,
            digest=digest,
            stream=True,
        )
        if resp is None:
            layer_cache[digest] = None
            return None
        tmp = tempfile.TemporaryFile()
        first_bytes = b''
        for chunk in resp.iter_content(chunk_size=65536):
            if not first_bytes:
                first_bytes = chunk[:2]
            tmp.write(chunk)
        tmp.seek(0)
        if first_bytes[:2] == b'\x1f\x8b':
            # gzip: decompress into a second tempfile so tarfile can seek
            decompressed = tempfile.TemporaryFile()
            with gzip.open(tmp) as gz:
                while True:
                    block = gz.read(65536)
                    if not block:
                        break
                    decompressed.write(block)
            tmp.close()
            decompressed.seek(0)
            layer_cache[digest] = decompressed
        else:
            layer_cache[digest] = tmp
        return layer_cache[digest]

    def read_file(path):
        norm = path.lstrip('/')
        for layer in layers:
            tmp = _load_layer(layer)
            if tmp is None:
                continue
            tmp.seek(0)
            with tarfile.open(fileobj=tmp) as tf:
                for member in tf.getmembers():
                    if member.name.lstrip('./') == norm and member.isfile():
                        fobj = tf.extractfile(member)
                        return fobj.read() if fobj else None
        return None

    return read_file


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
        import cryptography.x509 as x509
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
            try:
                pk = ser.load_der_public_key(der)
            except Exception:  # nosec B110
                # cbomkit-theia may store full X.509 cert DER instead of SubjectPublicKeyInfo
                cert = x509.load_der_x509_certificate(der)
                pk = cert.public_key()
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


def _enrich_orphan_algorithms(components):
    '''
    Fill parameterSetIdentifier for algorithm components not reachable via related-crypto-material.

    cbomkit-theia creates per-cert-file summary algorithm components (one RSA entry, one ECDSA
    entry per file) in addition to per-cert algorithm components linked by the key-material graph.
    The summary components share the same evidence locations as the per-cert ones but have no
    algorithmRef pointing to them, so _propagate_key_sizes cannot resolve them.

    After _propagate_key_sizes has run, the per-cert algorithm components at each location
    already carry parameterSetIdentifier.  Collect those resolved values and propagate to any
    orphan at the same location with the same algorithm family (using the minimum — most
    conservative — when multiple key sizes are present).

    Returns the number of algorithm components patched.
    '''
    from collections import defaultdict

    # location → algo_name → set of int sizes (from already-resolved algo components)
    resolved_by_loc = defaultdict(lambda: defaultdict(set))
    for comp in components:
        cp = comp.get('cryptoProperties') or {}
        if cp.get('assetType') != 'algorithm':
            continue
        ap = cp.get('algorithmProperties') or {}
        param_id = ap.get('parameterSetIdentifier')
        if not param_id:
            continue
        try:
            size = int(param_id)
        except (ValueError, TypeError):
            continue
        name = comp.get('name', '')
        for occ in (comp.get('evidence') or {}).get('occurrences') or []:
            loc = occ.get('location', '')
            if loc:
                resolved_by_loc[loc][name].add(size)

    if not resolved_by_loc:
        return 0

    patched = 0
    for comp in components:
        cp = comp.get('cryptoProperties') or {}
        if cp.get('assetType') != 'algorithm':
            continue
        ap = cp.get('algorithmProperties') or {}
        if ap.get('parameterSetIdentifier'):
            continue
        name = comp.get('name', '')
        for occ in (comp.get('evidence') or {}).get('occurrences') or []:
            loc = occ.get('location', '')
            if not loc:
                continue
            loc_sizes = resolved_by_loc.get(loc)
            if not loc_sizes:
                continue
            # Direct name match first, then substring (RSA ∈ SHA256withRSA, etc.)
            sizes = loc_sizes.get(name)
            if not sizes:
                for rname, rset in loc_sizes.items():
                    if name in rname or rname in name:
                        sizes = rset
                        break
            if not sizes:
                continue
            ap['parameterSetIdentifier'] = str(min(sizes))
            patched += 1
            break  # one location match per component is sufficient
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


_CA_BUNDLE_PATHS = (
    '/etc/ssl/',
    '/etc/pki/tls/',
    '/usr/share/ca-certificates/',
    '/usr/local/share/ca-certificates/',
)


def _tag_trust_store_components(components):
    '''
    Tag cryptographic-asset components whose every occurrence location is under a
    well-known OS CA-bundle path with gardener.cloud/cbom/source: os-trust-store.

    Returns the number of components tagged.
    '''
    tagged = 0
    for comp in components:
        if comp.get('type') != 'cryptographic-asset':
            continue
        occs = (comp.get('evidence') or {}).get('occurrences') or []
        locs = [occ.get('location', '') for occ in occs if occ.get('location')]
        if not locs:
            continue
        if not all(
            any(loc.startswith(prefix) for prefix in _CA_BUNDLE_PATHS)
            for loc in locs
        ):
            continue
        props = comp.setdefault('properties', [])
        if any(p.get('name') == 'gardener.cloud/cbom/source' for p in props):
            continue
        props.append({'name': 'gardener.cloud/cbom/source', 'value': 'os-trust-store'})
        tagged += 1
    return tagged


def _parse_os_release(data):
    '''Parse /etc/os-release bytes into a dict of key→value (quotes stripped).'''
    result = {}
    for line in data.decode('utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, _, val = line.partition('=')
        result[key.strip()] = val.strip().strip('"\'')
    return result


_OS_PURL_PREFIXES = (
    'pkg:apk/alpine/',
    'pkg:deb/debian/',
    'pkg:deb/ubuntu/',
    'pkg:rpm/rhel/',
)


def _has_os_purl(components):
    '''Return True if any component already carries an OS purl.'''
    for comp in components:
        purl = comp.get('purl', '')
        if any(purl.startswith(p) for p in _OS_PURL_PREFIXES):
            return True
    return False


def _synthesise_os_purl(fields):
    '''
    Derive (purl, name, version) from /etc/os-release fields, or None.

    Returns None when VERSION_ID is absent, empty, non-numeric, or the distro
    ID is not in the recognised set.
    '''
    distro_id = fields.get('ID', '').lower()
    version_id = fields.get('VERSION_ID', '')
    if not version_id or not version_id[0].isdigit():
        return None
    if distro_id == 'alpine':
        return f'pkg:apk/alpine/alpine@{version_id}', 'alpine', version_id
    if distro_id in ('debian', 'ubuntu'):
        return f'pkg:deb/{distro_id}/base-files@{version_id}', 'base-files', version_id
    if distro_id in ('rhel', 'centos') or distro_id.startswith('ubi'):
        return f'pkg:rpm/rhel/redhat-release@{version_id}', 'redhat-release', version_id
    return None


def enrich_sbom(cdx_bytes, file_reader):
    '''
    Inject a synthetic OS purl component into cdx_bytes if one is missing.

    Reads /etc/os-release via file_reader, parses ID and VERSION_ID, and appends
    a single "library" component tagged with gardener.cloud/sbom/source:
    os-release-injection.  Returns unchanged bytes when the purl already exists,
    /etc/os-release is unreadable, or the distro/version is unrecognised.
    '''
    doc = json.loads(cdx_bytes)
    components = doc.setdefault('components', [])

    if _has_os_purl(components):
        return cdx_bytes

    data = file_reader('/etc/os-release')
    if not data:
        return cdx_bytes

    result = _synthesise_os_purl(_parse_os_release(data))
    if not result:
        return cdx_bytes

    purl, name, version = result
    components.append({
        'type': 'library',
        'name': name,
        'version': version,
        'purl': purl,
        'properties': [
            {'name': 'gardener.cloud/sbom/source', 'value': 'os-release-injection'},
        ],
    })
    logger.info('SBOM enrichment: injected OS purl %s from /etc/os-release', purl)
    return json.dumps(doc, ensure_ascii=False).encode()


def enrich(cbom_bytes, image_reference=None, file_reader=None, oci_client=None):
    '''
    Return enriched CBOM bytes.

    Enrichment applied:
    1. Propagate key sizes from related-crypto-material components to their referenced
       algorithm components' parameterSetIdentifier (works without image access).
    2. If image_reference is given, inject RSA algorithm and key-material components for
       APK signing keys (.rsa.pub files) — using file_reader if provided, then Docker
       if available, then OCI layer download if oci_client is provided.

    file_reader, if given, must be a callable: path: str -> bytes | None.
    When provided, Docker and OCI paths are not attempted.
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

    orphaned = _enrich_orphan_algorithms(components)
    if orphaned:
        logger.info(
            'CBOM enrichment: resolved %d orphan algorithm component(s) from siblings',
            orphaned,
        )

    trust_store_tagged = _tag_trust_store_components(components)
    if trust_store_tagged:
        logger.info(
            'CBOM enrichment: tagged %d component(s) as os-trust-store source',
            trust_store_tagged,
        )

    if image_reference:
        if file_reader is not None:
            active_reader = file_reader
            cleanup = lambda: None
        else:
            active_reader, cleanup = _docker_file_reader(image_reference)
            if active_reader is None and oci_client is not None:
                active_reader = _oci_file_reader(image_reference, oci_client)
                cleanup = lambda: None
        try:
            if active_reader:
                new_comps = _enrich_apk_keys(components, active_reader)
                if new_comps:
                    components.extend(new_comps)
                    doc['components'] = components
                    logger.info(
                        'CBOM enrichment: injected %d component(s) for APK signing keys',
                        len(new_comps),
                    )
            else:
                logger.debug(
                    'CBOM enrichment: no file reader available, skipping APK key enrichment',
                )
        finally:
            cleanup()

    return json.dumps(doc, ensure_ascii=False).encode()
