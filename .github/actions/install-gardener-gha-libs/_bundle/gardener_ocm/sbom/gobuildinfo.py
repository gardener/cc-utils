# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0
'''
Go build-info extraction for OCI container images.

Reads the build-info blob the Go linker embeds into every executable (what `go version -m`
prints) and publishes it per binary as an OCI referrer of the scanned image. Unlike the
syft CycloneDX SBOM, this keeps the build settings that decide crypto behaviour at runtime:
`DefaultGODEBUG` (e.g. x509sha1 / tlssha1), `GOEXPERIMENT` (boringcrypto), `GOFIPS140`,
`CGO_ENABLED`, `-tags` and `vcs.revision`.

Pure Python: no Go toolchain, no syft-json. Only the inline build-info format (Go >= 1.18)
is supported; older binaries are reported with `go_version: null`.

Public interface
----------------
  TOOL_NAME                                         str
  ARTIFACT_TYPE                                     str
  ANALYSIS_METHOD_ANNOTATION                        str
  ANALYSIS_METHOD_VALUE                             str

  parse_buildinfo(fobj)                             -> dict | None
  scan_binaries(entries)                            -> list[dict]
  build_document(image_reference, binaries)         -> bytes
  push_referrer(doc_bytes, image_reference, oci_client) -> str
'''
import datetime
import json
import logging
import re

import oci.client as oc
import oci.model as om

import sbom.oci as soci

logger = logging.getLogger(__name__)

TOOL_NAME = 'go-buildinfo'
ARTIFACT_TYPE = 'application/vnd.gardener.go-buildinfo+json'
ANALYSIS_METHOD_ANNOTATION = 'gardener.cloud/cbom/analysis-method'
ANALYSIS_METHOD_VALUE = 'go-buildinfo'
SCHEMA_VERSION = 1

_MAGIC = b'\xff Go buildinf:'
_HEADER_LEN = 32
_ALIGN = 16
_FLAG_INLINE = 0x2

_CHUNK = 1 << 20
# inline strings follow the header directly; 1 MiB covers modinfo of any real binary
_TAIL = 1 << 20

# GODEBUG settings relevant for crypto posture; literals found in a binary beyond the
# copies of its DefaultGODEBUG hint at runtime overrides (os.Setenv("GODEBUG", ...))
WATCHED_GODEBUG = (
    'tlssha1=1',
    'x509sha1=1',
    'fips140=off',
    'fips140=on',
    'fips140=only',
)
# bytes kept around each literal hit to tell DefaultGODEBUG copies from other strings
_CTX = 48


def _uvarint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError('truncated build-info varint')
        b = buf[pos]
        pos += 1
        value |= (b & 0x7f) << shift
        if not b & 0x80:
            return value, pos
        shift += 7


def _read_bytes(buf: bytes, pos: int) -> tuple[bytes, int]:
    n, pos = _uvarint(buf, pos)
    if pos + n > len(buf):
        raise ValueError('truncated build-info string')
    return buf[pos:pos + n], pos + n


def _scan(fobj) -> tuple[bytes | None, list[tuple[bytes, bytes]]]:
    '''Stream fobj once; return (build-info blob or None, [(literal, context)]).

    Like debug/buildinfo, only accepts the magic at a 16-byte aligned file offset; the
    magic string also occurs unaligned in data sections of some binaries. Each context
    holds up to _CTX bytes on either side of a watched GODEBUG literal.
    '''
    literals = [lit.encode() for lit in WATCHED_GODEBUG]
    # every hit incl. its full context fits into some window
    keep = max(len(_MAGIC), 2 * _CTX + max(map(len, literals)))
    header = None
    hits = {}  # (file offset, literal) -> context
    tail = b''
    base = 0  # file offset of window[0]
    while True:
        chunk = fobj.read(_CHUNK)
        final = not chunk
        window = tail + chunk
        if header is None:
            idx = window.find(_MAGIC)
            while idx >= 0 and (base + idx) % _ALIGN:
                idx = window.find(_MAGIC, idx + 1)
            if idx >= 0:
                header = window[idx:idx + _HEADER_LEN + _TAIL]
        elif len(header) < _HEADER_LEN + _TAIL:
            # header blob may extend into following chunks
            header += chunk[:_HEADER_LEN + _TAIL - len(header)]
        for lit in literals:
            idx = window.find(lit)
            while idx >= 0:
                if final or idx + len(lit) + _CTX <= len(window):
                    hits.setdefault(
                        (base + idx, lit),
                        window[max(0, idx - _CTX):idx + len(lit) + _CTX],
                    )
                idx = window.find(lit, idx + 1)
        if final:
            return header, [(lit, ctx) for (_off, lit), ctx in hits.items()]
        tail = window[-keep:]
        base += len(window) - len(tail)


def _godebug_literals(hits, default_godebug: str) -> tuple[dict, dict]:
    '''
    Classify watched literals not explained by DefaultGODEBUG copies.

    Returns ({literal: count}, {literal: count}): standalone string constants (as in
    `os.Setenv("GODEBUG", "tlssha1=1")`, likely runtime overrides), and mentions directly
    preceded by `GODEBUG=` (help / error texts such as "override with GODEBUG=x509sha1=1").
    '''
    default = (default_godebug or '').encode()
    literals = {}
    mentions = {}
    for lit, ctx in hits:
        pos = ctx.find(lit)
        at = default.find(lit)
        explained = False
        while at >= 0 and not explained:
            # compare overlapping bytes left and right of the literal
            left = min(pos, at)
            right = min(len(ctx) - pos, len(default) - at)
            explained = ctx[pos - left:pos + right] == default[at - left:at + right]
            at = default.find(lit, at + 1)
        if explained:
            continue
        target = mentions if ctx[:pos].endswith(b'GODEBUG=') else literals
        key = lit.decode()
        target[key] = target.get(key, 0) + 1
    return literals, mentions


def _mod(fields: list[str]) -> dict:
    return {'path': fields[0], 'version': fields[1] if len(fields) > 1 else ''}


def _parse_modinfo(modinfo: str) -> dict:
    out = {'path': None, 'main_module': None, 'deps': [], 'settings': {}}
    for line in modinfo.splitlines():
        kind, _, rest = line.partition('\t')
        if kind == 'path':
            out['path'] = rest
        elif kind == 'mod':
            out['main_module'] = _mod(rest.split('\t'))
        elif kind == 'dep':
            out['deps'].append(_mod(rest.split('\t')))
        elif kind == '=>' and out['deps']:
            out['deps'][-1]['replace'] = _mod(rest.split('\t'))
        elif kind == 'build':
            key, _, value = rest.partition('=')
            out['settings'][key] = value
    return out


def parse_buildinfo(fobj) -> dict | None:
    '''
    Parse Go build info from a binary file object (sequential reads only).

    Returns None for non-Go files. For Go binaries predating the inline format (< 1.18)
    returns {'go_version': None}.
    '''
    buf, hits = _scan(fobj)
    if buf is None or len(buf) < _HEADER_LEN:
        return None
    if not buf[15] & _FLAG_INLINE:
        literals, mentions = _godebug_literals(hits, '')
        return {'go_version': None, 'godebug_literals': literals, 'godebug_mentions': mentions}
    version, pos = _read_bytes(buf, _HEADER_LEN)
    if not version:
        return None
    mod, _ = _read_bytes(buf, pos)
    # strip cmd/go/internal/modload infoStart / infoEnd sentinels (as debug/buildinfo)
    mod = mod[16:-16] if len(mod) >= 33 and mod[-17:-16] == b'\n' else b''
    info = _parse_modinfo(mod.decode('utf-8', errors='replace'))
    literals, mentions = _godebug_literals(hits, info['settings'].get('DefaultGODEBUG', ''))
    return {
        'go_version': version.decode('utf-8', errors='replace'),
        **info,
        'godebug_literals': literals,
        'godebug_mentions': mentions,
    }


def scan_binaries(entries) -> list[dict]:
    '''
    Parse build info for (path, fobj) pairs; non-Go files are skipped.

    Returns [{'binary': path, 'go_version': ..., 'path': ..., 'main_module': ...,
    'settings': {...}, 'deps': [...]}] sorted by binary path. Per-file read or parse
    errors are logged and skipped, iterator errors propagate.
    '''
    out = {}
    for path, fobj in entries:
        if path in out:
            continue
        try:
            info = parse_buildinfo(fobj)
        except (OSError, ValueError, IndexError) as exc:
            logger.warning('go-buildinfo: cannot parse %s: %s', path, exc)
            continue
        if info is not None:
            out[path] = {'binary': path, **info}
    return [out[p] for p in sorted(out)]


# text files: only small, non-binary ones are searched (entrypoints, env/unit files, ...)
_TEXT_MAX_SIZE = 1 << 20
_GODEBUG_ASSIGNMENT = re.compile(rb'\bGODEBUG\b["\']?\s*[=:]')


def scan_text_files(entries) -> list[dict]:
    '''
    Find GODEBUG assignments in (path, fobj) text files: sh `[export ]GODEBUG=...`, YAML /
    JSON `GODEBUG: ...` / `"GODEBUG": ...`, systemd `Environment=GODEBUG=...`, env files.
    Mere references (`$GODEBUG`, `os.Getenv("GODEBUG")`) do not match.

    Files larger than _TEXT_MAX_SIZE or containing NUL bytes (binary) are skipped.
    Returns [{'path': ..., 'lineno': ..., 'line': ...}], lines truncated to 200 chars.
    '''
    out = []
    for path, fobj in entries:
        data = fobj.read(_TEXT_MAX_SIZE + 1)
        if len(data) > _TEXT_MAX_SIZE or b'\0' in data or b'GODEBUG' not in data:
            continue
        for lineno, line in enumerate(data.splitlines(), start=1):
            if _GODEBUG_ASSIGNMENT.search(line):
                out.append({
                    'path': path,
                    'lineno': lineno,
                    'line': line.decode('utf-8', errors='replace').strip()[:200],
                })
    return out


def build_document(
    image_reference: str | om.OciImageReference,
    binaries: list[dict],
    godebug_assignments: list[dict] = (),
) -> bytes:
    return json.dumps({
        'schemaVersion': SCHEMA_VERSION,
        'tool': TOOL_NAME,
        'image': str(image_reference),
        'created': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'binaries': binaries,
        'godebug_assignments': list(godebug_assignments),
    }, indent=1).encode()


def push_referrer(
    doc_bytes: bytes,
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
) -> str:
    '''Push a go-buildinfo document as OCI referrer of `image_reference`; returns its digest.'''
    subject_digest, subject_media_type, subject_size, repo_ref = soci._resolve_subject(
        image_reference, oci_client,
    )
    return soci._push_referrer(
        doc_bytes=doc_bytes,
        doc_media_type=ARTIFACT_TYPE,
        artifact_type=ARTIFACT_TYPE,
        repo_ref=repo_ref,
        subject_digest=subject_digest,
        subject_media_type=subject_media_type,
        subject_size=subject_size,
        oci_client=oci_client,
        annotations={
            ANALYSIS_METHOD_ANNOTATION: ANALYSIS_METHOD_VALUE,
            'gardener.cloud/cbom/tool-version': TOOL_NAME,
        },
    )


def lookup_referrer(
    image_reference: str | om.OciImageReference,
    oci_client: oc.Client,
) -> dict | None:
    '''Return the newest go-buildinfo document attached to `image_reference`, or None.'''
    ref = om.OciImageReference.to_image_ref(image_reference)
    referrers = oci_client.referrers(
        image_reference=ref,
        artifact_type=ARTIFACT_TYPE,
        absent_ok=True,
    )
    if not referrers:
        return None
    repo = ref.ref_without_tag
    manifests = [
        json.loads(oci_client.manifest_raw(f'{repo}@{r.digest}').content)
        for r in referrers
    ]
    newest = max(
        manifests,
        key=lambda m: (m.get('annotations') or {}).get('org.opencontainers.image.created', ''),
    )
    blob = oci_client.blob(image_reference=repo, digest=newest['layers'][0]['digest'])
    return json.loads(blob.content)
