# SPDX-FileCopyrightText: 2025 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
BoringCrypto binary scan for Go executables.

Identifies Go binaries by the build-ID magic header and checks for
crypto/internal/boring import-path markers to determine whether the binary
was compiled with -tags boringcrypto.

Public interface
----------------
  TOOL_NAME                                    str
  ANALYSIS_METHOD_ANNOTATION                   str
  ANALYSIS_METHOD_VALUE                        str

  scan_binaries(entries)                       -> dict | None
  build_inferred_cbom(image_reference, result) -> bytes
'''
import datetime
import json
import logging

logger = logging.getLogger(__name__)

TOOL_NAME = 'boringcrypto-scan'
ANALYSIS_METHOD_ANNOTATION = 'gardener.cloud/cbom/analysis-method'
ANALYSIS_METHOD_VALUE = 'boringcrypto-scan'

# Go build-info magic header; survives stripping.
_GO_BUILD_ID_MAGIC = b'\xff Go build ID:'

# BoringCrypto markers: import path always present when linked; GOEXPERIMENT belt-and-suspenders.
_BORING_MARKERS = (
    b'crypto/internal/boring',
    b'GOEXPERIMENT=boringcrypto',
)

# Binary dirs to scan (matches elfcrypto.py).
_SEARCH_DIRS = frozenset({
    '/usr/local/bin', '/usr/bin', '/usr/sbin',
    '/bin', '/sbin', '/usr/local/sbin',
})

_SCAN_CHUNK = 65536
# Overlap ensures patterns that span a chunk boundary are still found.
_SCAN_OVERLAP = max(len(p) for p in (_GO_BUILD_ID_MAGIC,) + _BORING_MARKERS) - 1


def _scan_file(fobj, patterns):
    '''
    Stream-scan fobj for byte patterns using a sliding window.

    Reads in 64 KiB chunks with a tail buffer of max(len(p))-1 bytes.
    Never holds more than ~128 KiB in memory regardless of file size.
    Returns frozenset of the patterns found.
    '''
    remaining = set(patterns)
    found = set()
    tail = b''
    while remaining:
        chunk = fobj.read(_SCAN_CHUNK)
        if not chunk:
            break
        window = tail + chunk
        for p in list(remaining):
            if p in window:
                found.add(p)
                remaining.discard(p)
        tail = window[-_SCAN_OVERLAP:] if _SCAN_OVERLAP else b''
    return frozenset(found)


def scan_binaries(entries):
    '''
    Scan an iterable of (path, fobj) pairs for BoringCrypto markers.

    Each fobj must support sequential read(). I/O failures for individual
    entries are skipped with a debug log; iterator-level failures propagate
    to the caller.

    Returns {"boring_fips_module": bool, "go_binaries_scanned": int}
    or None if no Go binaries were found.
    '''
    all_patterns = (_GO_BUILD_ID_MAGIC,) + _BORING_MARKERS
    seen_paths = set()
    go_count = 0
    boring_found = False

    for path, fobj in entries:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            found = _scan_file(fobj, all_patterns)
        except Exception as exc:
            logger.debug('boringcrypto: read failed %s: %s', path, exc)
            continue
        if _GO_BUILD_ID_MAGIC not in found:
            continue
        go_count += 1
        if any(m in found for m in _BORING_MARKERS):
            boring_found = True

    if go_count == 0:
        return None

    return {
        'boring_fips_module': boring_found,
        'go_binaries_scanned': go_count,
    }


def build_inferred_cbom(image_reference, scan_result: dict) -> bytes:
    '''Build a CycloneDX 1.6 CBOM from a boringcrypto scan result dict.'''
    now = datetime.datetime.now(tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    boring = scan_result.get('boring_fips_module', False)
    scanned = scan_result.get('go_binaries_scanned', 0)
    doc = {
        'bomFormat':   'CycloneDX',
        'specVersion': '1.6',
        'version':     1,
        'metadata': {
            'timestamp': now,
            'tools': [{'name': TOOL_NAME, 'version': '0.1.0'}],
            'component': {
                'type': 'container',
                'name': str(image_reference),
                'properties': [
                    {'name': ANALYSIS_METHOD_ANNOTATION, 'value': ANALYSIS_METHOD_VALUE},
                ],
            },
            'properties': [
                {
                    'name': 'boring_fips_module',
                    'value': 'true' if boring else 'false',
                },
                {
                    'name': 'go_binaries_scanned',
                    'value': str(scanned),
                },
                {
                    'name': 'gardener.cloud/cbom/source-image',
                    'value': str(image_reference),
                },
            ],
        },
        'components': [],
    }
    return json.dumps(doc, indent=2).encode()
