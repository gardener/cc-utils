# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0
'''
ELF dynamic-symbol-based crypto inference for C/C++ OCI container images.

Complements the Go-binary inference in gobinary.py for images whose main
executables are C/C++ binaries linked against OpenSSL, BoringSSL, or mbedTLS.

Two extraction modes
--------------------
OCI-layer (primary, no Docker required):
  Fetches image layers via the OCI client, iterates tar members to locate
  executables in standard binary directories, extracts them to a tempdir,
  and runs `nm`/`readelf` analysis on each.  Requires an `oci.client.Client`
  instance to be passed to `infer_from_elf()`.

Docker (fallback):
  Uses `docker create` + `docker cp` when no OCI client is provided.

For statically-linked binaries (e.g. envoy with BoringSSL), dynamic imports
give no signal.  The scanner falls back to string-marker detection using
`strings`, looking for `external/boringssl/` source path markers embedded
in debug sections.

Public interface
----------------
  TOOL_NAME                                            str
  infer_from_elf(image_reference,
                 oci_client=None,
                 binary_paths=None)                   -> dict | None
'''
import datetime
import gzip
import io
import json
import logging
import os
import re
import subprocess
import tarfile
import tempfile

logger = logging.getLogger(__name__)

TOOL_NAME = 'elf-crypto-inference'
ANALYSIS_METHOD_ANNOTATION = 'gardener.cloud/cbom/analysis-method'
ANALYSIS_METHOD_VALUE = 'elf-crypto-inference'

# ---------------------------------------------------------------------------
# Symbol → crypto rules
# (symbol_name_fragment, algorithm_name or None, [protocol_names])
# Fragments are matched as exact name or as a prefix of the symbol name.
# ---------------------------------------------------------------------------

_SYMBOL_RULES = [
    # AES
    ('EVP_aes_128_gcm',          'AES-128-GCM',          []),
    ('EVP_aes_256_gcm',          'AES-256-GCM',          []),
    ('EVP_aes_128_cbc',          'AES-128-CBC',          []),
    ('EVP_aes_256_cbc',          'AES-256-CBC',          []),
    ('EVP_aes_128_ctr',          'AES-128-CTR',          []),
    ('EVP_aes_256_ctr',          'AES-256-CTR',          []),
    # RSA
    ('RSA_sign',                 'RSA',                  []),
    ('RSA_verify',               'RSA',                  []),
    ('RSA_public_encrypt',       'RSA',                  []),
    ('RSA_private_decrypt',      'RSA',                  []),
    ('EVP_PKEY_CTX_set_rsa',     'RSA',                  []),
    ('EVP_PKEY_encrypt',         'RSA',                  []),
    ('EVP_PKEY_decrypt',         'RSA',                  []),
    ('EVP_PKEY_sign',            'RSA',                  []),
    # ECDSA / EC
    ('ECDSA_sign',               'ECDSA',                []),
    ('ECDSA_verify',             'ECDSA',                []),
    ('EC_KEY_new',               'ECDSA',                []),
    # SHA
    ('EVP_sha1',                 'SHA-1',                []),
    ('EVP_sha256',               'SHA-256',              []),
    ('EVP_sha384',               'SHA-384',              []),
    ('EVP_sha512',               'SHA-512',              []),
    ('EVP_md5',                  'MD5',                  []),
    ('SHA256',                   'SHA-256',              []),
    ('SHA384',                   'SHA-384',              []),
    ('SHA512',                   'SHA-512',              []),
    # ChaCha20
    ('EVP_chacha20_poly1305',    'ChaCha20-Poly1305',    []),
    ('EVP_chacha20',             'ChaCha20',             []),
    # HMAC
    ('HMAC',                     'HMAC-SHA256',          []),
    ('EVP_MAC_fetch',            'HMAC-SHA256',          []),
    # TLS / SSL (OpenSSL 1.x / 3.x)
    ('SSL_CTX_new',              None,                   ['TLS/1.2', 'TLS/1.3']),
    ('TLS_client_method',        None,                   ['TLS/1.2', 'TLS/1.3']),
    ('TLS_server_method',        None,                   ['TLS/1.2', 'TLS/1.3']),
    ('TLS_method',               None,                   ['TLS/1.2', 'TLS/1.3']),
    ('SSL_CTX_set_min_proto',    None,                   ['TLS/1.2', 'TLS/1.3']),
    ('SSL_accept',               None,                   ['TLS/1.2', 'TLS/1.3']),
    ('SSL_connect',              None,                   ['TLS/1.2', 'TLS/1.3']),
    # DTLS
    ('DTLS_client_method',       None,                   ['DTLS/1.2']),
    ('DTLS_server_method',       None,                   ['DTLS/1.2']),
    # mbedTLS symbols
    ('mbedtls_ssl_init',         None,                   ['TLS/1.2', 'TLS/1.3']),
    ('mbedtls_aes_init',         'AES-128-GCM',          []),
    ('mbedtls_rsa_init',         'RSA',                  []),
    ('mbedtls_ecdsa_sign',       'ECDSA',                []),
    ('mbedtls_sha256_init',      'SHA-256',              []),
    ('mbedtls_sha512_init',      'SHA-512',              []),
    ('mbedtls_md_hmac',          'HMAC-SHA256',          []),
    # NSS
    ('PK11_CreateContextBySymKey', None,                 ['TLS/1.2', 'TLS/1.3']),
    ('SSL_ConfigServerCert',     None,                   ['TLS/1.2', 'TLS/1.3']),
    # libgnutls
    ('gnutls_init',              None,                   ['TLS/1.2', 'TLS/1.3']),
    ('gnutls_handshake',         None,                   ['TLS/1.2', 'TLS/1.3']),
]

_RULE_INDEX = {sym: (alg, protos) for sym, alg, protos in _SYMBOL_RULES if alg or protos}

# Shared-library names indicating dynamic OpenSSL/TLS linkage.
_TLS_LIBS = re.compile(r'lib(ssl|crypto|gnutls|mbedtls|mbedcrypto|nss)\b', re.IGNORECASE)

# String marker for statically-linked BoringSSL.
_BORINGSSL_MARKER = re.compile(r'external/boringssl/', re.IGNORECASE)
_BORINGSSL_FIPS_MARKER = re.compile(r'fipsmodule/', re.IGNORECASE)

_SEARCH_DIRS = frozenset({
    '/usr/local/bin', '/usr/bin', '/usr/sbin',
    '/bin', '/sbin', '/usr/local/sbin',
})

# Skip binaries larger than this when doing static marker scan (save time).
_STRINGS_MAX_BYTES = 256 * 1024 * 1024  # 256 MiB


# ---------------------------------------------------------------------------
# ELF analysis (file-level, Docker- and OCI-layer-agnostic)
# ---------------------------------------------------------------------------

def _is_elf(data: bytes) -> bool:
    return data[:4] == b'\x7fELF'


def _dynamic_imports(binary_path: str) -> list:
    '''Return list of undefined (imported) dynamic symbol names.'''
    try:
        result = subprocess.run(
            ['nm', '--dynamic', '--undefined-only', binary_path],  # nosec B607
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            symbols = []
            for line in result.stdout.splitlines():
                parts = line.split()
                if not parts:
                    continue
                name = parts[-1]
                if '@' in name:
                    name = name[:name.index('@')]
                symbols.append(name)
            return symbols
    except Exception as exc:
        logger.debug('elfcrypto: nm failed on %s: %s', binary_path, exc)

    # Fallback: readelf --dyn-syms
    try:
        result = subprocess.run(
            ['readelf', '--dyn-syms', '--wide', binary_path],  # nosec B607
            capture_output=True,
            text=True,
            timeout=30,
        )
        symbols = []
        for line in result.stdout.splitlines():
            if 'UND' not in line:
                continue
            parts = line.split()
            if len(parts) >= 8:
                name = parts[7]
                if '@' in name:
                    name = name[:name.index('@')]
                if name and name != '0':
                    symbols.append(name)
        return symbols
    except Exception as exc:
        logger.debug('elfcrypto: readelf fallback failed on %s: %s', binary_path, exc)
    return []


def _needed_libs(binary_path: str) -> list:
    try:
        result = subprocess.run(
            ['readelf', '-d', binary_path],  # nosec B607
            capture_output=True,
            text=True,
            timeout=15,
        )
        libs = []
        for line in result.stdout.splitlines():
            if 'NEEDED' in line:
                m = re.search(r'\[([^\]]+)\]', line)
                if m:
                    libs.append(m.group(1))
        return libs
    except Exception:
        return []


def _detect_boringssl_static(binary_path: str, file_size: int = 0) -> bool:
    if file_size > _STRINGS_MAX_BYTES:
        return False
    try:
        result = subprocess.run(
            ['strings', binary_path],  # nosec B607
            capture_output=True,
            text=True,
            timeout=60,
        )
        for line in result.stdout.splitlines():
            if _BORINGSSL_MARKER.search(line):
                return True
    except Exception:  # nosec B110
        pass
    return False


def _detect_boringssl_fips(binary_path: str, file_size: int = 0) -> bool:
    if file_size > _STRINGS_MAX_BYTES:
        return False
    try:
        result = subprocess.run(
            ['strings', binary_path],  # nosec B607
            capture_output=True,
            text=True,
            timeout=60,
        )
        for line in result.stdout.splitlines():
            if _BORINGSSL_FIPS_MARKER.search(line):
                return True
    except Exception:  # nosec B110
        pass
    return False


def _match_symbols(symbols: list) -> tuple:
    algs = set()
    protos = set()
    for sym in symbols:
        for fragment, (alg, sym_protos) in _RULE_INDEX.items():
            if sym == fragment or sym.startswith(fragment):
                if alg:
                    algs.add(alg)
                protos.update(sym_protos)
    return algs, protos


def _analyse_binary(binary_path: str, file_size: int = 0) -> tuple:
    '''
    Analyse one ELF binary.  Returns (alg_set, proto_set, boringssl, boringssl_fips).
    '''
    libs = _needed_libs(binary_path)
    has_dynamic_tls = any(_TLS_LIBS.search(lib) for lib in libs)

    syms = _dynamic_imports(binary_path)
    algs, protos = _match_symbols(syms)

    boringssl = False
    boringssl_fips = False

    if not has_dynamic_tls:
        boringssl = _detect_boringssl_static(binary_path, file_size)
        if boringssl:
            protos.update(['TLS/1.2', 'TLS/1.3'])
            algs.update([
                'AES-128-GCM', 'AES-256-GCM',
                'ChaCha20-Poly1305',
                'RSA', 'ECDSA',
                'SHA-256', 'SHA-384', 'SHA-512',
                'HMAC-SHA256',
            ])
            boringssl_fips = _detect_boringssl_fips(binary_path, file_size)
    elif not protos:
        # Dynamic TLS lib found but no specific TLS symbols resolved — emit defaults.
        protos.update(['TLS/1.2', 'TLS/1.3'])

    return algs, protos, boringssl, boringssl_fips


# ---------------------------------------------------------------------------
# OCI-layer extraction (primary)
# ---------------------------------------------------------------------------

def _resolve_manifest(image_ref, oci_client):
    '''Resolve a possibly multi-arch manifest to a single-arch OCI manifest.'''
    import oci.model as om
    ref = om.OciImageReference.to_image_ref(image_ref)
    repo_ref = ref.ref_without_tag
    manifest = oci_client.manifest(image_ref)
    if not isinstance(manifest, om.OciImageManifestList):
        return manifest, repo_ref
    entries = [
        e for e in manifest.manifests
        if e.platform and e.platform.os == 'linux'
        and e.platform.architecture == 'amd64'
    ]
    entry = entries[0] if entries else (
        manifest.manifests[0] if manifest.manifests else None
    )
    if entry is None:
        return None, repo_ref
    return oci_client.manifest(f'{repo_ref}@{entry.digest}'), repo_ref


def _entrypoint_paths(manifest, repo_ref, oci_client) -> set:
    '''
    Extract candidate paths from the image config's Entrypoint/Cmd.
    Returns a set that includes both the binary paths AND their parent dirs,
    so siblings of the entrypoint in non-standard bin dirs are also discovered.
    '''
    try:
        import json as _json
        config_digest = manifest.config.digest
        resp = oci_client.blob(
            image_reference=repo_ref,
            digest=config_digest,
            stream=False,
        )
        if resp is None:
            return set()
        cfg = _json.loads(resp.content)
        container_config = cfg.get('config') or cfg.get('container_config') or {}
        result = set()
        for field in ('Entrypoint', 'Cmd'):
            for entry in (container_config.get(field) or []):
                if entry and entry.startswith('/'):
                    result.add(entry)
                    result.add(os.path.dirname(entry))
        return result
    except Exception as exc:
        logger.debug('elfcrypto: config fetch failed: %s', exc)
        return set()


def _infer_via_oci(image_reference: str, oci_client, binary_paths, tmpdir: str) -> dict | None:
    '''
    Extract ELF binaries from OCI layers and run crypto analysis.

    Layer are fetched and decompressed once; tar members are scanned to
    locate executables in _SEARCH_DIRS plus any entrypoint paths from the
    image config.  Each layer is loaded at most once.
    '''
    try:
        manifest, repo_ref = _resolve_manifest(image_reference, oci_client)
    except Exception as exc:
        logger.debug('elfcrypto: OCI manifest fetch failed for %s: %s', image_reference, exc)
        return None

    if manifest is None:
        return None

    # Reverse layers so most-recent (topmost overlay) is checked first.
    layers = list(reversed(manifest.layers))
    layer_cache = {}   # digest -> decompressed tar bytes or None

    # Entrypoint paths augment _SEARCH_DIRS for non-standard binary locations
    # (e.g. fluent-bit at /fluent-bit/bin/fluent-bit).
    extra_paths = set(_entrypoint_paths(manifest, repo_ref, oci_client))

    def _load_layer(layer):
        digest = layer.digest
        if digest in layer_cache:
            return layer_cache[digest]
        try:
            resp = oci_client.blob(
                image_reference=repo_ref,
                digest=digest,
                stream=False,
            )
            if resp is None:
                layer_cache[digest] = None
                return None
            raw = resp.content
            data = gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw
            layer_cache[digest] = data
        except Exception as exc:
            logger.debug('elfcrypto: failed to load layer %s: %s', digest[:12], exc)
            layer_cache[digest] = None
        return layer_cache[digest]

    def _scan_layers_for_paths():
        '''Discover candidate binary paths from tar member headers.'''
        seen = set()
        candidates = []
        for layer in layers:
            data = _load_layer(layer)
            if data is None:
                continue
            try:
                with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                    for m in tf.getmembers():
                        if not m.isfile():
                            continue
                        path = '/' + m.name.lstrip('./')
                        if path in seen:
                            continue
                        seen.add(path)
                        dir_part = os.path.dirname(path)
                        if (dir_part in _SEARCH_DIRS
                                or dir_part in extra_paths
                                or path in extra_paths) and (m.mode & 0o111):
                            candidates.append((path, m.size))
            except Exception:  # nosec B110
                pass
        return candidates

    def _extract_binary(path, local_path):
        '''Extract a single file from its layer to local_path.'''
        norm = path.lstrip('/')
        for layer in layers:
            data = _load_layer(layer)
            if data is None:
                continue
            try:
                with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                    for m in tf.getmembers():
                        if m.name.lstrip('./') == norm and m.isfile():
                            fobj = tf.extractfile(m)
                            if fobj is None:
                                return False
                            content = fobj.read()
                            with open(local_path, 'wb') as out:
                                out.write(content)
                            return True
            except Exception:  # nosec B110
                pass
        return False

    if binary_paths:
        candidates = [(p, 0) for p in binary_paths]
    else:
        candidates = _scan_layers_for_paths()

    if not candidates:
        logger.debug('elfcrypto (OCI): no candidate binaries found in %s', image_reference)
        return None

    all_algs = set()
    all_protos = set()
    boringssl_detected = False
    boringssl_fips = False
    elf_count = 0

    for bpath, bsize in candidates:
        local = os.path.join(tmpdir, os.path.basename(bpath))
        if not _extract_binary(bpath, local):
            continue
        try:
            with open(local, 'rb') as f:
                header = f.read(4)
        except Exception:  # nosec B112
            continue
        if header != b'\x7fELF':
            continue
        elf_count += 1
        algs, protos, bs, bs_fips = _analyse_binary(local, bsize)
        all_algs.update(algs)
        all_protos.update(protos)
        if bs:
            boringssl_detected = True
        if bs_fips:
            boringssl_fips = True

    if not all_algs and not all_protos:
        return None

    return {
        'algorithms':     sorted(all_algs),
        'protocols':      sorted(all_protos),
        'boringssl':      boringssl_detected,
        'boringssl_fips': boringssl_fips,
        'binary_count':   elf_count,
        'source':         'elf-dynamic-symbols',
    }


# ---------------------------------------------------------------------------
# Docker fallback
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    try:
        subprocess.run(
            ['docker', 'info'],  # nosec B607
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception:
        return False


def _docker_entrypoint_paths(image_reference: str) -> set:
    '''
    Return set of directories (from entrypoint paths) + the entrypoint paths themselves.
    This allows discovering siblings of the entrypoint binary in non-standard bin dirs.
    '''
    try:
        import json as _json
        result = subprocess.run(
            ['docker', 'image', 'inspect', image_reference,  # nosec B607
             '--format', '{"Entrypoint":{{json .Config.Entrypoint}},"Cmd":{{json .Config.Cmd}}}'],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return set()
        data = _json.loads(result.stdout.strip())
        paths = set()
        dirs = set()
        for field in ('Entrypoint', 'Cmd'):
            for entry in (data.get(field) or []):
                if entry and entry.startswith('/'):
                    paths.add(entry)
                    dirs.add(os.path.dirname(entry))
        return paths | dirs   # both specific paths and their parent directories
    except Exception:
        return set()


def _discover_via_docker(image_reference: str) -> list:
    '''Return [(path, 0)] for executable binaries discovered via docker.'''
    extra_paths = _docker_entrypoint_paths(image_reference)

    # Try docker run with find (may fail if find is not in the image).
    try:
        result = subprocess.run(
            ['docker', 'run', '--rm', '--entrypoint', 'find',  # nosec B607
             image_reference] + list(_SEARCH_DIRS) + ['-type', 'f', '-executable'],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            found = [(p.strip(), 0) for p in result.stdout.splitlines() if p.strip()]
            # Add explicit entrypoint paths not already discovered
            found_paths = {p for p, _ in found}
            for p in extra_paths:
                if p not in found_paths:
                    found.append((p, 0))
            return found
    except Exception as exc:
        logger.debug('elfcrypto (Docker): find failed for %s: %s', image_reference, exc)

    # Fallback: docker create + docker export (scans the full filesystem).
    try:
        cid = subprocess.run(
            ['docker', 'create', image_reference],  # nosec B607
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        if not cid:
            return []
        try:
            tar_data = subprocess.run(
                ['docker', 'export', cid],  # nosec B607
                capture_output=True, timeout=120,
            ).stdout
            candidates = []
            with tarfile.open(fileobj=io.BytesIO(tar_data)) as tf:
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    path = '/' + m.name.lstrip('./')
                    dir_part = os.path.dirname(path)
                    if (dir_part in _SEARCH_DIRS
                            or dir_part in extra_paths
                            or path in extra_paths) and (m.mode & 0o111):
                        candidates.append((path, m.size))
            return candidates
        finally:
            subprocess.run(['docker', 'rm', cid], capture_output=True, timeout=10)  # nosec B607
    except Exception as exc:
        logger.debug('elfcrypto (Docker): export fallback failed for %s: %s',
                     image_reference, exc)
    return []


def _extract_via_docker(image_reference: str, binary_path: str, local_path: str) -> bool:
    try:
        cid = subprocess.run(
            ['docker', 'create', image_reference],  # nosec B607
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        if not cid:
            return False
        try:
            cp = subprocess.run(
                ['docker', 'cp', f'{cid}:{binary_path}', local_path],  # nosec B607
                capture_output=True, timeout=60,
            )
            return cp.returncode == 0
        finally:
            subprocess.run(['docker', 'rm', cid], capture_output=True, timeout=10)  # nosec B607
    except Exception as exc:
        logger.debug('elfcrypto (Docker): cp failed %s from %s: %s',
                     binary_path, image_reference, exc)
        return False


def _infer_via_docker(image_reference: str, binary_paths, tmpdir: str) -> dict | None:
    if not _docker_available():
        logger.debug('elfcrypto: Docker unavailable for %s', image_reference)
        return None

    if binary_paths:
        candidates = [(p, 0) for p in binary_paths]
    else:
        candidates = _discover_via_docker(image_reference)

    if not candidates:
        return None

    all_algs = set()
    all_protos = set()
    boringssl_detected = False
    boringssl_fips = False
    elf_count = 0

    for bpath, bsize in candidates:
        local = os.path.join(tmpdir, os.path.basename(bpath))
        if not _extract_via_docker(image_reference, bpath, local):
            continue
        try:
            with open(local, 'rb') as f:
                header = f.read(4)
        except Exception:  # nosec B112
            continue
        if header != b'\x7fELF':
            continue
        elf_count += 1
        file_size = os.path.getsize(local)
        algs, protos, bs, bs_fips = _analyse_binary(local, file_size)
        all_algs.update(algs)
        all_protos.update(protos)
        if bs:
            boringssl_detected = True
        if bs_fips:
            boringssl_fips = True

    if not all_algs and not all_protos:
        return None

    return {
        'algorithms':     sorted(all_algs),
        'protocols':      sorted(all_protos),
        'boringssl':      boringssl_detected,
        'boringssl_fips': boringssl_fips,
        'binary_count':   elf_count,
        'source':         'elf-dynamic-symbols',
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

_FIPS_ALGS = frozenset({
    'AES-128-GCM', 'AES-256-GCM', 'AES-128-CTR', 'AES-256-CTR',
    'RSA', 'ECDSA', 'SHA-256', 'SHA-384', 'SHA-512',
    'HMAC-SHA256', 'HMAC-SHA384', 'HMAC-SHA512',
})


def _alg_to_primitive(name: str) -> str:
    n = name.upper()
    if 'AES' in n:     return 'AE'
    if 'CHACHA' in n:  return 'AE'
    if 'ECDSA' in n:   return 'SIGNATURE'
    if 'RSA' in n:     return 'PKE'
    if 'ECDH' in n:    return 'KA'
    if 'ED25519' in n: return 'SIGNATURE'
    if 'HMAC' in n:    return 'MAC'
    if 'SHA' in n:     return 'HASH'
    if 'MD5' in n:     return 'HASH'
    if 'BCRYPT' in n:  return 'HASH'
    if 'ARGON' in n:   return 'HASH'
    return 'OTHER'


def _parse_proto(proto: str) -> tuple:
    if '/' in proto:
        t, v = proto.split('/', 1)
        return t, v
    return proto, ''


def infer_from_elf(
    image_reference: str,
    oci_client=None,
    binary_paths: list | None = None,
) -> dict | None:
    '''
    Return {algorithms, protocols, boringssl, boringssl_fips, binary_count, source}
    inferred from ELF dynamic imports and (for static binaries) string markers.

    oci_client: oci.client.Client — preferred extraction path; no Docker needed.
      If None, falls back to Docker.
    binary_paths: list of absolute paths inside the image to inspect.
      If None, paths are discovered automatically.
    Returns None if no crypto signals are found or neither extraction path works.
    '''
    with tempfile.TemporaryDirectory() as tmpdir:
        if oci_client is not None:
            result = _infer_via_oci(image_reference, oci_client, binary_paths, tmpdir)
            if result is not None:
                return result
            logger.debug(
                'elfcrypto: OCI path returned nothing for %s, trying Docker',
                image_reference,
            )
        return _infer_via_docker(image_reference, binary_paths, tmpdir)


def build_inferred_cbom(image_ref: str, inference: dict) -> bytes:
    '''
    Build a CycloneDX 1.6 CBOM from an ELF inference result dict.

    The document is tagged with `analysis-method: elf-crypto-inference` so it
    can be distinguished from cbomkit-theia and go-binary-inference referrers.
    '''
    components = []

    for alg_name in inference.get('algorithms', []):
        comp = {
            'type': 'cryptographic-asset',
            'name': alg_name,
            'cryptoProperties': {
                'assetType': 'algorithm',
                'algorithmProperties': {
                    'primitive': _alg_to_primitive(alg_name),
                },
            },
        }
        if inference.get('boringssl_fips') and alg_name in _FIPS_ALGS:
            comp['cryptoProperties']['certificationLevel'] = ['FIPS140-2', 'FIPS140-3']
        components.append(comp)

    for proto in inference.get('protocols', []):
        proto_type, proto_ver = _parse_proto(proto)
        components.append({
            'type': 'cryptographic-asset',
            'name': proto,
            'cryptoProperties': {
                'assetType': 'protocol',
                'protocolProperties': {
                    'type':    proto_type,
                    'version': proto_ver,
                },
            },
        })

    now = datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    properties = [
        {'name': ANALYSIS_METHOD_ANNOTATION, 'value': ANALYSIS_METHOD_VALUE},
        {'name': 'gardener.cloud/cbom/source-image', 'value': str(image_ref)},
        {'name': 'gardener.cloud/cbom/inference-source', 'value': inference.get('source', '')},
        {'name': 'gardener.cloud/cbom/elf-binary-count',
         'value': str(inference.get('binary_count', 0))},
    ]
    if inference.get('boringssl'):
        properties.append({'name': 'gardener.cloud/cbom/boringssl-detected', 'value': 'true'})
    if inference.get('boringssl_fips'):
        properties.append({'name': 'gardener.cloud/cbom/boringssl-fips-detected', 'value': 'true'})

    doc = {
        'bomFormat':   'CycloneDX',
        'specVersion': '1.6',
        'version':     1,
        'metadata': {
            'timestamp': now,
            'tools': [{'name': TOOL_NAME, 'version': '0.1.0'}],
            'properties': properties,
        },
        'components': components,
    }
    return json.dumps(doc, indent=2).encode()
