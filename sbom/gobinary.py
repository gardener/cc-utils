# SPDX-FileCopyrightText: Copyright Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0
'''
Go module-based crypto inference for OCI container images.

Complements cbomkit-theia, which is filesystem-level only and produces zero findings for
scratch/distroless images that contain only compiled Go binaries, and misleading CA-bundle
noise for Debian-based images.

Primary approach — SBOM-driven (no extra I/O)
---------------------------------------------
`infer_from_cdx(cdx_bytes)` parses the CycloneDX SBOM that syft has already produced,
extracts `pkg:golang/...` purl components, and maps known crypto-relevant Go modules to
algorithms and protocols.  Both the pipeline integration (scan_image in inject.py) and the
post-hoc scan (scan_gobinary.py) use this path — no additional layer downloads or subprocess
calls are needed.

Limitation: BoringCrypto (FIPS-validated Go crypto backend) detection requires scanning
binary bytes for linker-embedded markers, which are not captured by syft.  The SBOM-driven
path emits `boringcrypto: false`; a separate binary-analysis run is needed for FIPS
certification annotations.

Public interface
----------------
  TOOL_NAME                  str  identifier used in tool-version annotations
  ANALYSIS_METHOD_ANNOTATION str  OCI manifest annotation key
  ANALYSIS_METHOD_VALUE      str  OCI manifest annotation value

  infer_from_cdx(cdx_bytes)                        -> dict | None
  fetch_cdx_sbom(image_ref, oci_client)             -> bytes | None
  build_inferred_cbom(image_ref, inference)          -> bytes
  has_inferred_cbom(image_ref, oci_client)           -> bool
'''
import datetime
import json
import logging

import oci.client as oc
import oci.model as om

logger = logging.getLogger(__name__)

TOOL_NAME = 'go-binary-inference'
ANALYSIS_METHOD_ANNOTATION = 'gardener.cloud/cbom/analysis-method'
ANALYSIS_METHOD_VALUE = 'go-binary-inference'

# artifact type shared with cbomkit-theia; distinguished by manifest annotation
CBOM_ARTIFACT_TYPE = 'application/vnd.cyclonedx+json;profile=cbom'
_CDX_SBOM_ARTIFACT_TYPE = 'application/vnd.cyclonedx+json'

_PURL_GOLANG_PREFIX = 'pkg:golang/'


# ---------------------------------------------------------------------------
# Module → crypto inference rules
# (prefix, algorithms, protocols, description)
# ---------------------------------------------------------------------------

_CRYPTO_MODULE_RULES = [
    ('golang.org/x/crypto',
     ['AES', 'ChaCha20', 'ChaCha20-Poly1305', 'BLAKE2s', 'BLAKE2b',
      'Ed25519', 'X25519', 'HKDF', 'SHA-256', 'SHA-512', 'bcrypt', 'scrypt', 'Argon2'],
     [],
     'golang.org/x/crypto: extended primitives'),

    ('k8s.io/apiserver',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'AES-256-GCM', 'ChaCha20-Poly1305', 'SHA-256', 'SHA-384'],
     ['TLS/1.2', 'TLS/1.3'],
     'k8s.io/apiserver: TLS server + mTLS client auth'),

    ('k8s.io/client-go',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'AES-256-GCM', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'k8s.io/client-go: TLS to API server'),

    ('k8s.io/kms',
     ['AES-128-GCM', 'AES-256-GCM', 'RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'k8s.io/kms: encrypted secrets at rest + gRPC TLS'),

    ('go.etcd.io/etcd',
     ['AES-128-GCM', 'AES-256-GCM', 'RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'etcd: TLS client + peer communication'),

    ('google.golang.org/grpc',
     ['AES-128-GCM', 'AES-256-GCM', 'ChaCha20-Poly1305', 'ECDSA', 'RSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'gRPC: TLS transport'),

    ('github.com/go-jose/go-jose',
     ['RSA', 'ECDSA', 'AES-128-KW', 'AES-256-KW', 'AES-128-GCM', 'AES-256-GCM',
      'SHA-256', 'SHA-384', 'SHA-512', 'HMAC-SHA256', 'HMAC-SHA384', 'HMAC-SHA512'],
     [],
     'go-jose: JOSE (JWE/JWS) primitives'),

    ('github.com/golang-jwt/jwt',
     ['RSA', 'ECDSA', 'Ed25519', 'HMAC-SHA256', 'HMAC-SHA384', 'HMAC-SHA512', 'SHA-256'],
     [],
     'golang-jwt: JWT signing'),

    ('github.com/coreos/go-oidc',
     ['RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'go-oidc: OIDC verification (HTTPS + JWT)'),

    ('golang.org/x/oauth2',
     ['RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'OAuth2: HTTPS + token signing'),

    ('github.com/containerd/containerd',
     ['AES-128-GCM', 'RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'containerd: TLS registry communication'),

    ('github.com/docker/distribution',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'docker/distribution: TLS registry communication'),

    ('github.com/cert-manager',
     ['RSA', 'ECDSA', 'Ed25519', 'SHA-256', 'SHA-384', 'AES-128-GCM'],
     ['TLS/1.2', 'TLS/1.3'],
     'cert-manager: PKI / certificate management'),

    ('sigs.k8s.io/controller-runtime',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'controller-runtime: TLS webhook server'),

    ('github.com/projectcalico',
     ['AES-128-GCM', 'RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'Calico: TLS for API + Felix'),

    ('golang.org/x/net/ssh',
     ['RSA', 'ECDSA', 'Ed25519', 'AES-128-CTR', 'AES-256-CTR',
      'ChaCha20-Poly1305', 'SHA-256', 'HMAC-SHA256'],
     ['SSH/2'],
     'golang.org/x/net/ssh: SSH protocol'),

    ('github.com/aws/aws-sdk-go-v2',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'AES-256-GCM', 'SHA-256', 'SHA-384', 'HMAC-SHA256'],
     ['TLS/1.2', 'TLS/1.3'],
     'AWS SDK v2: HTTPS + SigV4 signing'),

    ('github.com/aws/aws-sdk-go',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'AES-256-GCM', 'SHA-256', 'SHA-384', 'HMAC-SHA256'],
     ['TLS/1.2', 'TLS/1.3'],
     'AWS SDK v1: HTTPS + SigV4 signing'),

    ('github.com/aws/smithy-go',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256', 'HMAC-SHA256'],
     ['TLS/1.2', 'TLS/1.3'],
     'smithy-go: AWS service client transport (TLS)'),

    ('github.com/aliyun/alibaba-cloud-sdk-go',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256', 'HMAC-SHA256'],
     ['TLS/1.2', 'TLS/1.3'],
     'Alibaba Cloud SDK: HTTPS + request signing'),

    ('github.com/alibabacloud-go',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256', 'HMAC-SHA256'],
     ['TLS/1.2', 'TLS/1.3'],
     'alibabacloud-go: Alibaba Cloud service clients (TLS)'),

    ('github.com/weaveworks/common',
     ['AES-128-GCM', 'AES-256-GCM', 'ChaCha20-Poly1305', 'RSA', 'ECDSA', 'SHA-256', 'HMAC-SHA256'],
     ['TLS/1.2', 'TLS/1.3'],
     'weaveworks/common: gRPC/HTTP server+client, TLS, JWT auth'),

    ('github.com/cortexproject/cortex',
     ['AES-128-GCM', 'AES-256-GCM', 'ChaCha20-Poly1305', 'RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'cortex: gRPC TLS for inter-component communication'),

    ('github.com/credativ/vali',
     ['AES-128-GCM', 'AES-256-GCM', 'ChaCha20-Poly1305', 'RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'vali: Loki-fork log aggregation — gRPC TLS'),

    ('github.com/grafana/loki',
     ['AES-128-GCM', 'AES-256-GCM', 'ChaCha20-Poly1305', 'RSA', 'ECDSA', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'Loki: gRPC TLS for log aggregation'),

    ('github.com/fluent/fluent-operator',
     ['RSA', 'ECDSA', 'AES-128-GCM', 'SHA-256'],
     ['TLS/1.2', 'TLS/1.3'],
     'fluent-operator: k8s webhook TLS'),
]

# Deduplicated rule index: prefix → (alg_set, proto_set, first_description)
_RULE_INDEX = {}
for _prefix, _algs, _protos, _note in _CRYPTO_MODULE_RULES:
    if _prefix not in _RULE_INDEX:
        _RULE_INDEX[_prefix] = (set(), set(), _note)
    _RULE_INDEX[_prefix][0].update(_algs)
    _RULE_INDEX[_prefix][1].update(_protos)


_FIPS_ALGS = frozenset({
    'AES-128-GCM', 'AES-256-GCM', 'AES-128-CTR', 'AES-256-CTR',
    'RSA', 'ECDSA', 'SHA-256', 'SHA-384', 'SHA-512',
    'HMAC-SHA256', 'HMAC-SHA384', 'HMAC-SHA512',
})


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

def _infer_crypto(module_paths: dict) -> tuple:
    '''
    Return (alg_paths, proto_paths) inferred from module_paths.

    module_paths maps module_path -> [binary_paths] (from syft evidence.occurrences).
    alg_paths / proto_paths map each inferred name -> set of binary paths from all
    modules that contributed it.
    '''
    alg_paths = {}
    proto_paths = {}
    for mod, bin_paths in module_paths.items():
        for prefix, (rule_algs, rule_protos, _) in _RULE_INDEX.items():
            if mod == prefix or mod.startswith(prefix + '/') or mod.startswith(prefix + '@'):
                for alg in rule_algs:
                    alg_paths.setdefault(alg, set()).update(bin_paths)
                for proto in rule_protos:
                    proto_paths.setdefault(proto, set()).update(bin_paths)
                break
    return alg_paths, proto_paths


def _modules_from_cdx(doc: dict) -> dict:
    '''
    Extract Go module paths from a parsed CycloneDX SBOM document.

    Returns a dict mapping module_path -> list[binary_path] where binary_path
    is taken from each component's evidence.occurrences (populated by syft when
    it finds a Go module compiled into a specific binary).  The list is empty
    when no occurrence data is present.
    '''
    modules = {}
    for comp in doc.get('components', []):
        purl = comp.get('purl', '')
        if not purl.startswith(_PURL_GOLANG_PREFIX):
            continue
        mod_path = purl[len(_PURL_GOLANG_PREFIX):]
        if '@' in mod_path:
            mod_path = mod_path[:mod_path.index('@')]
        bin_paths = [
            occ['location']
            for occ in (comp.get('evidence') or {}).get('occurrences') or []
            if occ.get('location')
        ]
        modules[mod_path] = bin_paths
    return modules


# ---------------------------------------------------------------------------
# CycloneDX CBOM builder
# ---------------------------------------------------------------------------

def _alg_to_primitive(name: str) -> str:
    n = name.upper()
    if 'AES' in n:     return 'AE'
    if 'CHACHA' in n:  return 'AE'
    if 'ECDSA' in n:   return 'SIGNATURE'
    if 'RSA' in n:     return 'PKE'
    if 'ECDH' in n:    return 'KA'
    if 'X25519' in n:  return 'KA'
    if 'ED25519' in n: return 'SIGNATURE'
    if 'HMAC' in n:    return 'MAC'
    if 'SHA' in n:     return 'HASH'
    if 'BLAKE' in n:   return 'HASH'
    if 'BCRYPT' in n:  return 'HASH'
    if 'SCRYPT' in n:  return 'HASH'
    if 'ARGON' in n:   return 'HASH'
    if 'HKDF' in n:    return 'KDF'
    return 'OTHER'


def _parse_proto(proto: str) -> tuple:
    if '/' in proto:
        t, v = proto.split('/', 1)
        return t, v
    return proto, ''


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_from_cdx(cdx_bytes: bytes) -> dict | None:
    '''
    Infer crypto usage from a syft CycloneDX SBOM.

    Parses `pkg:golang/...` purl components and applies the module→crypto inference
    rules.  No network I/O, no subprocess calls, no binary access.

    Returns an inference dict:
      { 'algorithms': [...], 'protocols': [...],
        'algorithm_paths': {alg: [path, ...]}, 'protocol_paths': {proto: [path, ...]},
        'boringcrypto': False, 'source': 'cdx-sbom', 'module_count': N }
    or None when no Go modules are present in the SBOM.

    algorithm_paths / protocol_paths map each inferred name to the sorted list of
    binary file paths (from syft evidence.occurrences) of the Go binaries in which
    the contributing module was found.  The lists are empty when the SBOM carries no
    occurrence data.

    Note: BoringCrypto detection is not possible from the SBOM; the flag is always
    False.  Binary-level scanning is needed for FIPS certification annotations.
    '''
    try:
        doc = json.loads(cdx_bytes)
    except Exception as exc:
        logger.warning('gobinary: cannot parse CycloneDX SBOM: %s', exc)
        return None

    modules = _modules_from_cdx(doc)
    if not modules:
        return None

    alg_paths, proto_paths = _infer_crypto(modules)
    return {
        'algorithms':      sorted(alg_paths),
        'protocols':       sorted(proto_paths),
        'algorithm_paths': {k: sorted(v) for k, v in alg_paths.items()},
        'protocol_paths':  {k: sorted(v) for k, v in proto_paths.items()},
        'boringcrypto':    False,
        'source':          'cdx-sbom',
        'module_count':    len(modules),
    }


def fetch_cdx_sbom(
    image_ref: str | om.OciImageReference,
    oci_client: oc.Client,
) -> bytes | None:
    '''
    Fetch the CycloneDX SBOM referrer for an image from the OCI registry.

    Returns the raw SBOM document bytes, or None if no referrer is found or fetching fails.
    Used by the post-hoc scan to avoid pulling image layers.
    '''
    try:
        ref = om.OciImageReference.to_image_ref(image_ref)
        repo = ref.ref_without_tag
        referrers = oci_client.referrers(
            image_reference=image_ref,
            artifact_type=_CDX_SBOM_ARTIFACT_TYPE,
            absent_ok=True,
        )
        if not referrers:
            return None
        manifest_bytes = oci_client.manifest_raw(f'{repo}@{referrers[0].digest}').content
        manifest = json.loads(manifest_bytes)
        blob_digest = manifest['layers'][0]['digest']
        return oci_client.blob(image_reference=repo, digest=blob_digest).content
    except Exception as exc:
        logger.debug('gobinary: cannot fetch CycloneDX SBOM for %s: %s', image_ref, exc)
        return None


def build_inferred_cbom(
    image_ref: str | om.OciImageReference,
    inference: dict,
) -> bytes:
    '''
    Build a CycloneDX 1.6 CBOM from an inference result dict.

    The `inference` dict is the output of `infer_from_cdx()`.  The document is tagged
    with `analysis-method: go-binary-inference` in both metadata properties and the
    referrer manifest annotation (the caller is responsible for the latter).
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
        if inference.get('boringcrypto') and alg_name in _FIPS_ALGS:
            comp['cryptoProperties']['nistQuantumSecurityLevel'] = 0
            comp['cryptoProperties']['certificationLevel'] = ['FIPS140-2', 'FIPS140-3']
        paths = inference.get('algorithm_paths', {}).get(alg_name, [])
        if paths:
            comp['evidence'] = {'occurrences': [{'location': p} for p in paths]}
        components.append(comp)

    for proto in inference.get('protocols', []):
        proto_type, proto_ver = _parse_proto(proto)
        comp = {
            'type': 'cryptographic-asset',
            'name': proto,
            'cryptoProperties': {
                'assetType': 'protocol',
                'protocolProperties': {
                    'type':    proto_type,
                    'version': proto_ver,
                },
            },
        }
        paths = inference.get('protocol_paths', {}).get(proto, [])
        if paths:
            comp['evidence'] = {'occurrences': [{'location': p} for p in paths]}
        components.append(comp)

    now = datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    properties = [
        {'name': ANALYSIS_METHOD_ANNOTATION, 'value': ANALYSIS_METHOD_VALUE},
        {'name': 'gardener.cloud/cbom/source-image', 'value': str(image_ref)},
        {'name': 'gardener.cloud/cbom/inference-source', 'value': inference.get('source', '')},
        {'name': 'gardener.cloud/cbom/go-module-count',
         'value': str(inference.get('module_count', 0))},
    ]
    if inference.get('boringcrypto'):
        properties.append(
            {'name': 'gardener.cloud/cbom/boringcrypto-detected', 'value': 'true'},
        )

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


def has_inferred_cbom(
    image_ref: str | om.OciImageReference,
    oci_client: oc.Client,
) -> bool:
    '''
    Return True if the image already has a go-binary-inferred CBOM referrer.

    Checks the OCI referrers API for any CBOM referrer annotated with
    analysis-method: go-binary-inference.  Fast: no layer or blob downloads.
    '''
    try:
        referrers = oci_client.referrers(
            image_reference=image_ref,
            artifact_type=CBOM_ARTIFACT_TYPE,
            absent_ok=True,
        )
    except Exception as exc:
        logger.debug('gobinary: referrers check failed for %s: %s', image_ref, exc)
        return False

    if not referrers:
        return False

    return any(
        r.annotations.get(ANALYSIS_METHOD_ANNOTATION) == ANALYSIS_METHOD_VALUE
        for r in referrers
    )
