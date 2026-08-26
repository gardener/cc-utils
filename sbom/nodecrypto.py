# SPDX-FileCopyrightText: Copyright Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0
'''
Node.js package-based crypto inference for OCI container images.

Detects cryptographic usage in Node.js applications by inspecting which
crypto-relevant npm packages are installed in `node_modules/` directories
or declared in `package.json` dependencies.

Any Node.js app that makes HTTPS requests uses TLS 1.2/1.3 via the Node.js
built-in `tls` module backed by OpenSSL (or BoringSSL on some builds).

Two extraction modes
--------------------
OCI-layer (primary, no Docker required):
  Fetches image layers, scans tar member paths for `node_modules/` directories
  and `package.json` files, then extracts and parses dependency lists.

Docker (fallback):
  Uses `docker run ls` / `docker cp` when no OCI client is provided.

Public interface
----------------
  TOOL_NAME                              str
  infer_from_node(image_reference,
                  oci_client=None)       -> dict | None
'''
import datetime
import gzip
import io
import json
import logging
import os
import subprocess
import tarfile

logger = logging.getLogger(__name__)

TOOL_NAME = 'node-crypto-inference'
ANALYSIS_METHOD_ANNOTATION = 'gardener.cloud/cbom/analysis-method'
ANALYSIS_METHOD_VALUE = 'node-crypto-inference'

# ---------------------------------------------------------------------------
# Package → crypto inference rules
# ---------------------------------------------------------------------------
# Maps npm package name (exact or prefix) to ([algorithms], [protocols]).

_NODE_CRYPTO_PACKAGES = {
    # JWE/JWS/JWT (JOSE) — RSA-OAEP, AES-GCM, ECDH, ECDSA, EdDSA, HMAC
    'jose': (
        ['RSA', 'ECDSA', 'AES-128-GCM', 'AES-256-GCM', 'SHA-256', 'SHA-384', 'HMAC-SHA256'],
        [],
    ),
    # Classic JWT: HS256/RS256/ES256/EdDSA
    'jsonwebtoken': (
        ['RSA', 'ECDSA', 'HMAC-SHA256', 'SHA-256'],
        [],
    ),
    'express-jwt': (
        ['RSA', 'ECDSA', 'HMAC-SHA256'],
        [],
    ),
    # Pure-JS TLS/PKI
    'node-forge': (
        ['RSA', 'AES-128-GCM', 'AES-256-GCM', 'AES-128-CBC', 'SHA-256', 'ECDSA', 'HMAC-SHA256'],
        ['TLS/1.2'],
    ),
    # Symmetric + hash
    'crypto-js': (
        ['AES', 'SHA-256', 'HMAC-SHA256', 'RSA'],
        [],
    ),
    # Password hashing
    'bcrypt': (
        ['bcrypt'],
        [],
    ),
    'bcryptjs': (
        ['bcrypt'],
        [],
    ),
    'argon2': (
        ['Argon2'],
        [],
    ),
    # HTTPS proxy agents — imply TLS transport
    'https-proxy-agent': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    # Octokit GitHub SDK (HTTPS)
    '@octokit/core': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    '@octokit/auth-app': (
        ['RSA', 'SHA-256'],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    'universal-github-app-jwt': (
        ['RSA', 'SHA-256'],
        [],
    ),
    # Kubernetes client (always TLS)
    '@kubernetes/client-node': (
        ['RSA', 'ECDSA', 'SHA-256'],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    # AWS SDK (HTTPS + SigV4)
    'aws-sdk': (
        ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256', 'HMAC-SHA256'],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    '@aws-sdk/client': (
        ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256', 'HMAC-SHA256'],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    # gRPC — always TLS
    '@grpc/grpc-js': (
        ['RSA', 'ECDSA', 'AES-128-GCM', 'AES-256-GCM', 'SHA-256'],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    'grpc': (
        ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256'],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    # OpenTelemetry gRPC exporter
    '@opentelemetry/exporter-grpc': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    '@opentelemetry/exporter-otlp-grpc': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    # HTTP(S) clients
    'axios': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    'node-fetch': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    'got': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    # OAuth2 / OIDC
    'passport-oauth2': (
        ['RSA', 'ECDSA', 'SHA-256'],
        ['TLS/1.2', 'TLS/1.3'],
    ),
    # socket.io — TLS when served over HTTPS
    'socket.io': (
        [],
        ['TLS/1.2', 'TLS/1.3'],
    ),
}

# Sorted longest-first for unambiguous prefix matching.
_PACKAGE_RULES = sorted(
    [(name, algs, protos) for name, (algs, protos) in _NODE_CRYPTO_PACKAGES.items()],
    key=lambda x: -len(x[0]),
)

# node_modules search roots (checked in order).
_NODE_MODULES_DIRS = [
    '/app/node_modules',
    '/usr/local/lib/node_modules',
    '/home/node/app/node_modules',
    '/node_modules',
    '/srv/node_modules',
]

# Directories that may contain the `node` binary.
_NODE_BIN_DIRS = ['/usr/local/bin', '/usr/bin', '/usr/local/nvm/current/bin']


# ---------------------------------------------------------------------------
# Shared package matching
# ---------------------------------------------------------------------------

def _match_packages(package_names: list) -> tuple:
    algs = set()
    protos = set()
    matched = []
    for pkg in package_names:
        for prefix, rule_algs, rule_protos in _PACKAGE_RULES:
            if pkg == prefix or pkg.startswith(prefix + '/') or pkg.startswith(prefix + '-'):
                algs.update(rule_algs)
                protos.update(rule_protos)
                matched.append(pkg)
                break
    return algs, protos, matched


def _parse_deps_from_package_json(data: bytes) -> list:
    try:
        doc = json.loads(data)
    except Exception:
        return []
    deps = {}
    for key in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
        deps.update(doc.get(key) or {})
    return list(deps.keys())


# ---------------------------------------------------------------------------
# OCI-layer extraction (primary)
# ---------------------------------------------------------------------------

def _resolve_manifest(image_ref, oci_client):
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


def _infer_via_oci(image_reference: str, oci_client) -> dict | None:
    try:
        manifest, repo_ref = _resolve_manifest(image_reference, oci_client)
    except Exception as exc:
        logger.debug('nodecrypto: OCI manifest fetch failed for %s: %s', image_reference, exc)
        return None
    if manifest is None:
        return None

    layers = list(reversed(manifest.layers))
    layer_cache = {}

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
            logger.debug('nodecrypto: failed to load layer %s: %s', digest[:12], exc)
            layer_cache[digest] = None
        return layer_cache[digest]

    has_node = False
    node_modules_pkgs = []    # package names from node_modules listing
    pkg_json_deps = []        # deps from package.json files

    # Single pass over all layers to collect everything we need.
    seen_paths = set()
    for layer in layers:
        data = _load_layer(layer)
        if data is None:
            continue
        try:
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                for m in tf.getmembers():
                    norm = '/' + m.name.lstrip('./')
                    if norm in seen_paths:
                        continue
                    seen_paths.add(norm)

                    # Check for node binary
                    if not has_node:
                        bn = os.path.basename(norm)
                        if bn in ('node', 'nodejs') and m.isfile() and (m.mode & 0o111):
                            has_node = True

                    # Collect node_modules top-level package names
                    parts = norm.split('/')
                    for root in _NODE_MODULES_DIRS:
                        root_parts = root.rstrip('/').split('/')
                        n = len(root_parts)
                        if (parts[:n] == root_parts
                                and len(parts) == n + 2
                                and m.isdir()):
                            node_modules_pkgs.append(parts[n])
                        # Scoped packages: @scope/name
                        elif (parts[:n] == root_parts
                                and len(parts) == n + 3
                                and parts[n].startswith('@')
                                and m.isdir()):
                            node_modules_pkgs.append(f'{parts[n]}/{parts[n+1]}')

                    # Parse top-level package.json (not inside node_modules or .yarn)
                    if (norm.endswith('/package.json')
                            and 'node_modules' not in norm
                            and '.yarn' not in norm
                            and m.isfile()):
                        try:
                            fobj = tf.extractfile(m)
                            if fobj:
                                pkg_json_deps.extend(_parse_deps_from_package_json(fobj.read()))
                        except Exception:  # nosec B110
                            pass
        except Exception:  # nosec B110
            pass

    if not has_node:
        logger.debug('nodecrypto (OCI): no Node.js binary found in %s', image_reference)
        return None

    all_pkgs = list(set(node_modules_pkgs + pkg_json_deps))
    base_protos = {'TLS/1.2', 'TLS/1.3'}
    algs, protos, matched = _match_packages(all_pkgs)
    protos.update(base_protos)

    return {
        'algorithms':       sorted(algs),
        'protocols':        sorted(protos),
        'matched_packages': matched,
        'package_count':    len(all_pkgs),
        'source':           'node-crypto-inference',
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


def _infer_via_docker(image_reference: str) -> dict | None:
    if not _docker_available():
        return None

    try:
        cid = subprocess.run(
            ['docker', 'create', image_reference],  # nosec B607
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        if not cid:
            return None
        try:
            tar_data = subprocess.run(
                ['docker', 'export', cid],  # nosec B607
                capture_output=True, timeout=120,
            ).stdout
        finally:
            subprocess.run(['docker', 'rm', cid], capture_output=True, timeout=10)  # nosec B607
    except Exception as exc:
        logger.debug('nodecrypto (Docker): export failed for %s: %s', image_reference, exc)
        return None

    has_node = False
    node_modules_pkgs = []
    pkg_json_deps = []
    seen_paths = set()
    node_modules_set = frozenset(_NODE_MODULES_DIRS)

    try:
        with tarfile.open(fileobj=io.BytesIO(tar_data)) as tf:
            for m in tf.getmembers():
                norm = '/' + m.name.lstrip('./')
                if norm in seen_paths:
                    continue
                seen_paths.add(norm)

                if not has_node:
                    bn = os.path.basename(norm)
                    if bn in ('node', 'nodejs') and m.isfile() and (m.mode & 0o111):
                        has_node = True

                parts = norm.split('/')
                for root in node_modules_set:
                    root_parts = root.rstrip('/').split('/')
                    n = len(root_parts)
                    if parts[:n] == root_parts and len(parts) == n + 2 and m.isdir():
                        node_modules_pkgs.append(parts[n])
                    elif (parts[:n] == root_parts
                            and len(parts) == n + 3
                            and parts[n].startswith('@')
                            and m.isdir()):
                        node_modules_pkgs.append(f'{parts[n]}/{parts[n+1]}')

                if (norm.endswith('/package.json')
                        and 'node_modules' not in norm
                        and '.yarn' not in norm
                        and m.isfile()):
                    try:
                        fobj = tf.extractfile(m)
                        if fobj:
                            pkg_json_deps.extend(_parse_deps_from_package_json(fobj.read()))
                    except Exception:  # nosec B110
                        pass
    except Exception as exc:
        logger.debug('nodecrypto (Docker): tar scan failed for %s: %s', image_reference, exc)
        return None

    if not has_node:
        logger.debug('nodecrypto (Docker): no Node.js binary in %s', image_reference)
        return None

    all_pkgs = list(set(node_modules_pkgs + pkg_json_deps))
    base_protos = {'TLS/1.2', 'TLS/1.3'}
    algs, protos, matched = _match_packages(all_pkgs)
    protos.update(base_protos)

    return {
        'algorithms':       sorted(algs),
        'protocols':        sorted(protos),
        'matched_packages': matched,
        'package_count':    len(all_pkgs),
        'source':           'node-crypto-inference',
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def infer_from_node(image_reference: str, oci_client=None) -> dict | None:
    '''
    Return {algorithms, protocols, matched_packages, package_count, source}
    inferred from Node.js package analysis.

    oci_client: oci.client.Client — preferred extraction path; no Docker needed.
      If None, falls back to Docker.
    Returns None if no Node.js runtime is found or neither extraction path works.
    '''
    if oci_client is not None:
        result = _infer_via_oci(image_reference, oci_client)
        if result is not None:
            return result
        logger.debug('nodecrypto: OCI path returned nothing for %s, trying Docker',
                     image_reference)
    return _infer_via_docker(image_reference)


def _alg_to_primitive(name: str) -> str:
    n = name.upper()
    if 'AES' in n:     return 'AE'
    if 'CHACHA' in n:  return 'AE'
    if 'ECDSA' in n:   return 'SIGNATURE'
    if 'RSA' in n:     return 'PKE'
    if 'HMAC' in n:    return 'MAC'
    if 'SHA' in n:     return 'HASH'
    if 'BCRYPT' in n:  return 'HASH'
    if 'ARGON' in n:   return 'HASH'
    return 'OTHER'


def _parse_proto(proto: str) -> tuple:
    if '/' in proto:
        t, v = proto.split('/', 1)
        return t, v
    return proto, ''


def build_inferred_cbom(image_ref: str, inference: dict) -> bytes:
    '''
    Build a CycloneDX 1.6 CBOM from a Node.js inference result dict.

    The document is tagged with `analysis-method: node-crypto-inference` so it
    can be distinguished from cbomkit-theia and elf-crypto-inference referrers.
    '''
    components = []

    for alg_name in inference.get('algorithms', []):
        components.append({
            'type': 'cryptographic-asset',
            'name': alg_name,
            'cryptoProperties': {
                'assetType': 'algorithm',
                'algorithmProperties': {
                    'primitive': _alg_to_primitive(alg_name),
                },
            },
        })

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
    matched = inference.get('matched_packages', [])
    properties = [
        {'name': ANALYSIS_METHOD_ANNOTATION,               'value': ANALYSIS_METHOD_VALUE},
        {'name': 'gardener.cloud/cbom/source-image',       'value': str(image_ref)},
        {'name': 'gardener.cloud/cbom/inference-source',
         'value': inference.get('source', '')},
        {'name': 'gardener.cloud/cbom/node-package-count',
         'value': str(inference.get('package_count', 0))},
        {'name': 'gardener.cloud/cbom/node-matched-packages', 'value': ','.join(matched)},
    ]

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
