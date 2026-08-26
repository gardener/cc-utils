# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Syft-based SBOM scanning and cbomkit-theia-based CBOM scanning for OCI images.

For each image:
  1. Check the target registry for existing SPDX + CycloneDX referrer manifests.
  2. Cache hit: download both SBOM blobs from the target; run cbomkit-theia on the
     CycloneDX blob to produce a CBOM (no image re-download needed).
  3. Cache miss: run syft, push both SBOM referrer manifests to the target; then run
     cbomkit-theia on the resulting CycloneDX output to produce and push the CBOM.

Scan admission mirrors a resource-aware approach:
  disk:   compressed_layer_bytes * 5.0
  memory: 200 MiB + compressed_layer_bytes * 2.0
Minimum headroom: 2 GiB disk, 1 GiB memory.  At least one scan is always admitted.
'''
import concurrent.futures
import gzip
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import zlib

import oci.client as oc
import oci.model as om
import ocm
import sbom.boringcrypto as sboring
import sbom.cbom as scbom
import sbom.cbomenrich as scbe
import sbom.elfcrypto as selfc
import sbom.gobinary as sgob
import sbom.nodecrypto as snodec
import sbom.oci as soci
import sbom.s3 as ss3

_DOCKER_CONFIG_PATH = os.path.expanduser('~/.docker/config.json')

logger = logging.getLogger(__name__)

_DISK_HEADROOM  = 2 * 1024 * 1024 * 1024   # 2 GiB
_MEM_HEADROOM   = 1 * 1024 * 1024 * 1024   # 1 GiB
_DISK_FACTOR    = 5.0
_MEM_BASE       = 200 * 1024 * 1024         # 200 MiB
_MEM_FACTOR     = 2.0


def check_syft():
    '''Verify syft is on PATH; raise RuntimeError with a friendly message if not.'''
    try:
        subprocess.run(  # nosec B607
            ['syft', 'version'],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            'syft is not installed or not on PATH. '
            'Please install syft (https://github.com/anchore/syft) before running CTT '
            'with SBOM injection enabled.'
        )


def check_cbomkit_theia():
    '''Verify cbomkit-theia is on PATH; raise RuntimeError with a friendly message if not.'''
    try:
        subprocess.run(  # nosec B607
            ['cbomkit-theia', '--help'],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            'cbomkit-theia is not installed or not on PATH. '
            'Please install cbomkit-theia (https://github.com/IBM/cbomkit-theia) before '
            'running CTT with SBOM/CBOM injection enabled.'
        )


def _cbomkit_theia_version() -> str | None:
    try:
        result = subprocess.run(  # nosec B607
            ['cbomkit-theia', 'version'],
            capture_output=True,
            text=True,
        )
        for line in (result.stdout + result.stderr).splitlines():
            parts = line.split()
            if parts:
                return parts[-1]
    except Exception:  # nosec B110
        pass
    return None


def _run_cbomkit_theia(
    image_ref: str,
    cdx_bom_path: str,
    out_path: str,
    tmpdir: str,
) -> None:
    '''
    Run cbomkit-theia on `image_ref`, enriching `cdx_bom_path` (CycloneDX SBOM) with
    cryptographic findings. Output is written to `out_path`.
    '''
    env = os.environ.copy()
    env['TMPDIR'] = tmpdir
    with open(out_path, 'w') as out_f:
        subprocess.run(  # nosec B607
            [
                'cbomkit-theia', 'image',
                '--bom', cdx_bom_path,
                image_ref,
            ],
            check=True,
            stdout=out_f,
            env=env,
        )


def _syft_version() -> str | None:
    try:
        result = subprocess.run(  # nosec B607
            ['syft', 'version', '--output', 'text'],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            if 'version' in line.lower():
                parts = line.split()
                if parts:
                    return parts[-1]
    except Exception:  # nosec B110
        pass
    return None


def _syft_version_from_spdx(spdx_bytes: bytes) -> str | None:
    try:
        doc = json.loads(spdx_bytes)
        for creator in doc.get('creationInfo', {}).get('creators', []):
            if creator.startswith('Tool: syft-'):
                return creator[len('Tool: syft-'):]
    except Exception:  # nosec B110
        pass
    return None


def _available_disk_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def _available_mem_bytes() -> int:
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    return 0


def _estimate_bytes(compressed_layer_bytes: int) -> tuple[int, int]:
    '''Return (estimated_disk_bytes, estimated_mem_bytes) for one syft invocation.'''
    return (
        int(compressed_layer_bytes * _DISK_FACTOR),
        int(_MEM_BASE + compressed_layer_bytes * _MEM_FACTOR),
    )


def _compressed_layer_bytes(image_ref: str | om.OciImageReference, oci_client: oc.Client) -> int:
    '''
    Fetch the manifest (resolving multi-arch to linux/amd64) and return the sum of
    compressed layer sizes.  Returns 0 on error (scan will still be admitted with force=True).
    '''
    try:
        manifest = oci_client.manifest(
            image_ref,
            accept=om.MimeTypes.prefer_multiarch,
        )
        if isinstance(manifest, om.OciImageManifestList):
            # pick linux/amd64 or fall back to first entry
            entries = [
                e for e in manifest.manifests
                if e.platform and e.platform.os == 'linux'
                and e.platform.architecture == 'amd64'
            ]
            entry = entries[0] if entries else (
                manifest.manifests[0] if manifest.manifests else None
            )
            if entry is None:
                return 0
            manifest = oci_client.manifest(
                f'{om.OciImageReference.to_image_ref(image_ref).ref_without_tag}@{entry.digest}',
            )
        return sum(layer.size for layer in manifest.layers)
    except Exception:  # nosec
        return 0


def lookup_sbom_referrers(
    image_ref: str | om.OciImageReference,
    oci_client: oc.Client,
) -> tuple[bytes, bytes, str, str] | None:
    '''
    Check the target for existing SPDX + CycloneDX referrer manifests.

    Returns (spdx_bytes, cdx_bytes, spdx_referrer_digest, cdx_referrer_digest)
    if both are present, otherwise None.
    `image_ref` should already be digest-addressed.
    '''
    image_ref = om.OciImageReference.to_image_ref(image_ref)
    repo_ref = image_ref.ref_without_tag

    spdx_referrers = oci_client.referrers(
        image_reference=image_ref,
        artifact_type=soci.SPDX_JSON_MEDIA_TYPE,
        absent_ok=True,
    )
    cdx_referrers = oci_client.referrers(
        image_reference=image_ref,
        artifact_type=soci.CYCLONEDX_JSON_MEDIA_TYPE,
        absent_ok=True,
    )

    # None means the referrers API is not supported; () means supported but no entries
    if not spdx_referrers or not cdx_referrers:
        return None

    spdx_descriptor = spdx_referrers[0]
    cdx_descriptor = cdx_referrers[0]

    try:
        spdx_manifest_digest = spdx_descriptor.digest
        cdx_manifest_digest = cdx_descriptor.digest

        def _download_sbom_blob(manifest_digest: str) -> bytes:
            manifest_bytes = oci_client.manifest_raw(
                f'{repo_ref}@{manifest_digest}',
            ).content
            manifest = json.loads(manifest_bytes)
            blob_digest = manifest['layers'][0]['digest']
            return oci_client.blob(
                image_reference=repo_ref,
                digest=blob_digest,
            ).content

        spdx_bytes = _download_sbom_blob(spdx_manifest_digest)
        cdx_bytes = _download_sbom_blob(cdx_manifest_digest)
        return spdx_bytes, cdx_bytes, spdx_manifest_digest, cdx_manifest_digest
    except Exception as e:
        logger.warning(f'failed to download existing SBOM blobs from {repo_ref}: {e}')
        return None


def _syft_docker_config_dir(tmpdir: str) -> str:
    '''
    Write a docker config without credHelpers (syft uses Docker credential helpers
    which may require interactive auth).  Returns a dir suitable for DOCKER_CONFIG.
    '''
    try:
        with open(_DOCKER_CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        return os.path.dirname(_DOCKER_CONFIG_PATH)

    cfg.pop('credHelpers', None)
    cfg.pop('credsStore', None)

    cfg_dir = tempfile.mkdtemp(dir=tmpdir)
    with open(os.path.join(cfg_dir, 'config.json'), 'w') as f:
        json.dump(cfg, f)
    return cfg_dir


def _oci_file_reader(image_ref, oci_client):
    '''
    Return (read_file, cleanup) using OCI layer extraction.

    Fetches image layers on demand and searches them for the requested path.
    Layers are scanned top-to-bottom (most recent overlay first); results are
    cached per layer so each blob is downloaded at most once.

    read_file(path: str) -> bytes | None
    cleanup() -> None  — no-op
    '''
    try:
        ref = om.OciImageReference.to_image_ref(image_ref)
        repo_ref = ref.ref_without_tag
        manifest = oci_client.manifest(image_ref)
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
                return None, lambda: None
            manifest = oci_client.manifest(f'{repo_ref}@{entry.digest}')
        layers = list(reversed(manifest.layers))
    except Exception:
        logger.debug('CBOM enrichment: failed to fetch manifest for OCI file reader')
        return None, lambda: None

    layer_cache = {}  # digest -> bytes | None  (decompressed tar content)

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
            # detect gzip by magic bytes; also covers Docker manifest media types
            if raw[:2] == b'\x1f\x8b':
                data = gzip.decompress(raw)
            else:
                data = raw
            layer_cache[digest] = data
        except Exception:
            layer_cache[digest] = None
        return layer_cache[digest]

    def read_file(path):
        norm = path.lstrip('/')
        for layer in layers:
            data = _load_layer(layer)
            if data is None:
                continue
            try:
                with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                    for member in tf.getmembers():
                        if member.name.lstrip('./') == norm and member.isfile():
                            fobj = tf.extractfile(member)
                            return fobj.read() if fobj else None
            except Exception:  # nosec B112
                continue
        return None

    return read_file, lambda: None


class _ChainReader:
    '''Prepends `head` bytes before delegating reads to `tail`.'''
    __slots__ = ('_head', '_tail')

    def __init__(self, head, tail):
        self._head = head
        self._tail = tail

    def read(self, n=-1):
        if self._head:
            if n < 0:
                data = self._head + self._tail.read()
                self._head = b''
                return data
            chunk = self._head[:n]
            self._head = self._head[n:]
            return chunk
        return self._tail.read(n) if n >= 0 else self._tail.read()

    def close(self):
        pass


def _decompress_layer(resp, dst):
    '''
    Stream-decompress an OCI layer blob into dst (writable file-like).

    Detects gzip (magic 1f 8b), zstd (magic 28 b5 2f fd), or plain tar
    from the first four bytes; requires neither resp.content nor a
    second full-size buffer.
    '''
    header = resp.raw.read(4)
    if not header:
        return
    if header[:4] == b'\x28\xb5\x2f\xfd':
        import zstandard
        with zstandard.ZstdDecompressor().stream_reader(_ChainReader(header, resp.raw)) as r:
            shutil.copyfileobj(r, dst, 65536)
    elif header[:2] == b'\x1f\x8b':
        d = zlib.decompressobj(wbits=47)  # 47 = auto-detect gzip
        dst.write(d.decompress(header))
        while True:
            chunk = resp.raw.read(65536)
            if not chunk:
                break
            dst.write(d.decompress(chunk))
        dst.write(d.flush())
    else:
        dst.write(header)
        shutil.copyfileobj(resp.raw, dst, 65536)


def _iter_boring_candidates(image_ref, oci_client):
    '''
    Yield (path, fobj) pairs for executable candidates in Go binary search dirs.

    Streams each layer to a TemporaryFile to avoid double-buffering the full
    decompressed content in memory.  Yields in overlay order (topmost layer first)
    so the caller sees the correct file for each path without needing to understand
    the overlay structure.  Raises on manifest fetch or layer I/O failure.
    '''
    import oci.model as om
    ref = om.OciImageReference.to_image_ref(image_ref)
    repo_ref = ref.ref_without_tag
    manifest = oci_client.manifest(image_ref)
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
            return
        manifest = oci_client.manifest(f'{repo_ref}@{entry.digest}')

    seen_paths = set()

    for layer in reversed(manifest.layers):
        with tempfile.TemporaryFile() as tmp_f:
            resp = oci_client.blob(
                image_reference=repo_ref,
                digest=layer.digest,
                stream=True,
            )
            try:
                _decompress_layer(resp, tmp_f)
            finally:
                resp.close()
            tmp_f.seek(0)
            with tarfile.open(fileobj=tmp_f) as tf:
                for member in tf:
                    if not member.isfile():
                        continue
                    path = '/' + member.name.lstrip('./')
                    if path in seen_paths:
                        continue
                    dir_part = os.path.dirname(path)
                    if dir_part not in sboring._SEARCH_DIRS:
                        continue
                    if not (member.mode & 0o111):
                        continue
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        continue
                    seen_paths.add(path)
                    yield path, fobj


def scan_image(
    image_ref: str | om.OciImageReference,
    oci_client: oc.Client,
    tmpdir: str,
    tool_ver: str | None = None,
) -> tuple[bytes, bytes, bytes, str | None, str | None, str, str, str]:
    '''
    Scan the image with syft and cbomkit-theia, push all three referrer manifests to the
    target, and return:
      (spdx_bytes, cdx_bytes, cbom_bytes, tool_ver, cbom_tool_ver,
       spdx_referrer_digest, cdx_referrer_digest, cbom_referrer_digest)

    `image_ref` should be digest-addressed.
    '''
    image_ref = om.OciImageReference.to_image_ref(image_ref)
    env = os.environ.copy()
    env['TMPDIR'] = tmpdir
    env['DOCKER_CONFIG'] = _syft_docker_config_dir(tmpdir)

    with tempfile.TemporaryDirectory(dir=tmpdir) as tmp:
        spdx_path = os.path.join(tmp, 'sbom.spdx.json')
        cdx_path = os.path.join(tmp, 'sbom.cdx.json')
        cbom_path = os.path.join(tmp, 'cbom.cdx.json')

        subprocess.run(  # nosec B607
            [
                'syft', 'scan', str(image_ref),
                '-o', f'spdx-json={spdx_path}',
                '-o', f'cyclonedx-json@1.6={cdx_path}',
            ],
            check=True,
            env=env,
        )

        with open(spdx_path, 'rb') as f:
            spdx_bytes = f.read()
        with open(cdx_path, 'rb') as f:
            cdx_bytes = f.read()

        _run_cbomkit_theia(
            image_ref=str(image_ref),
            cdx_bom_path=cdx_path,
            out_path=cbom_path,
            tmpdir=tmpdir,
        )
        with open(cbom_path, 'rb') as f:
            cbom_bytes = f.read()

        cbom_bytes = scbe.enrich(
            cbom_bytes,
            image_reference=str(image_ref),
            file_reader=_oci_file_reader(image_ref, oci_client)[0],
        )

    resolved_tool_ver = tool_ver or _syft_version_from_spdx(spdx_bytes)
    cbom_tool_ver = _cbomkit_theia_version()

    spdx_referrer_digest, cdx_referrer_digest = soci.push_sbom_referrers(
        spdx_bytes=spdx_bytes,
        cdx_bytes=cdx_bytes,
        image_reference=image_ref,
        oci_client=oci_client,
        tool_version=resolved_tool_ver,
    )
    cbom_referrer_digest = scbom.push_cbom_referrer(
        cbom_bytes=cbom_bytes,
        image_reference=image_ref,
        oci_client=oci_client,
        tool_version=cbom_tool_ver,
    )

    # Go module inference — complements cbomkit-theia for scratch/distroless images and
    # Debian-based images where cbomkit-theia only finds OS CA-bundle certs.
    # Uses the CycloneDX SBOM already in memory; no extra I/O.
    inference = sgob.infer_from_cdx(cdx_bytes)
    if inference:
        inferred_cbom_bytes = sgob.build_inferred_cbom(
            image_ref=image_ref,
            inference=inference,
        )
        scbom.push_cbom_referrer(
            cbom_bytes=inferred_cbom_bytes,
            image_reference=image_ref,
            oci_client=oci_client,
            tool_version=sgob.TOOL_NAME,
            extra_annotations={sgob.ANALYSIS_METHOD_ANNOTATION: sgob.ANALYSIS_METHOD_VALUE},
        )
        logger.info(
            '%s: pushed go-module-inferred CBOM (%d modules → %d algorithms, %d protocols)',
            image_ref,
            inference['module_count'],
            len(inference['algorithms']),
            len(inference['protocols']),
        )

    # ELF symbol inference — detects crypto in C/C++ images (envoy, fluent-bit, etc.)
    # that cbomkit-theia only covers as CA-bundle noise.
    elf_inference = selfc.infer_from_elf(
        image_reference=str(image_ref),
        oci_client=oci_client,
    )
    if elf_inference:
        elf_cbom_bytes = selfc.build_inferred_cbom(
            image_ref=image_ref,
            inference=elf_inference,
        )
        scbom.push_cbom_referrer(
            cbom_bytes=elf_cbom_bytes,
            image_reference=image_ref,
            oci_client=oci_client,
            tool_version=selfc.TOOL_NAME,
            extra_annotations={selfc.ANALYSIS_METHOD_ANNOTATION: selfc.ANALYSIS_METHOD_VALUE},
        )
        logger.info(
            '%s: pushed elf-inferred CBOM (%d binaries → %d algorithms, %d protocols%s)',
            image_ref,
            elf_inference['binary_count'],
            len(elf_inference['algorithms']),
            len(elf_inference['protocols']),
            ', BoringSSL' if elf_inference.get('boringssl') else '',
        )

    # Node.js inference — detects crypto in Node.js images (gardener-dashboard, etc.)
    node_inference = snodec.infer_from_node(
        image_reference=str(image_ref),
        oci_client=oci_client,
    )
    if node_inference:
        node_cbom_bytes = snodec.build_inferred_cbom(
            image_ref=image_ref,
            inference=node_inference,
        )
        scbom.push_cbom_referrer(
            cbom_bytes=node_cbom_bytes,
            image_reference=image_ref,
            oci_client=oci_client,
            tool_version=snodec.TOOL_NAME,
            extra_annotations={snodec.ANALYSIS_METHOD_ANNOTATION: snodec.ANALYSIS_METHOD_VALUE},
        )
        logger.info(
            '%s: pushed node-inferred CBOM (%d packages → %d algorithms, %d protocols)',
            image_ref,
            node_inference['package_count'],
            len(node_inference['algorithms']),
            len(node_inference['protocols']),
        )

    # BoringCrypto scan — detects whether Go binaries were built with -tags boringcrypto.
    boring_result = sboring.scan_binaries(
        _iter_boring_candidates(str(image_ref), oci_client)
    )
    if boring_result is not None:
        boring_cbom = sboring.build_inferred_cbom(str(image_ref), boring_result)
        scbom.push_cbom_referrer(
            cbom_bytes=boring_cbom,
            image_reference=image_ref,
            oci_client=oci_client,
            tool_version=sboring.TOOL_NAME,
            extra_annotations={
                sboring.ANALYSIS_METHOD_ANNOTATION: sboring.ANALYSIS_METHOD_VALUE,
            },
        )
        logger.info(
            '%s: pushed boringcrypto CBOM (%d Go binaries, boring_fips_module=%s)',
            image_ref,
            boring_result['go_binaries_scanned'],
            boring_result['boring_fips_module'],
        )

    return (
        spdx_bytes, cdx_bytes, cbom_bytes,
        resolved_tool_ver, cbom_tool_ver,
        spdx_referrer_digest, cdx_referrer_digest, cbom_referrer_digest,
    )


def run_injections_resource_aware(
    items: list[tuple[str, str | om.OciImageReference]],
    oci_client: oc.Client,
    tmpdir: str,
    tool_ver: str | None = None,
) -> list[tuple[str, bytes, bytes, bytes, str | None, str | None, str, str, str, str]]:
    '''
    Scan images with resource-aware admission control.

    `items` is a sequence of (resource_name, digest_image_ref) pairs for images that
    had a cache miss (no existing referrers).

    Returns a list of
      (resource_name, spdx_bytes, cdx_bytes, cbom_bytes,
       tool_ver, cbom_tool_ver,
       spdx_referrer_digest, cdx_referrer_digest, cbom_referrer_digest, status)
    where status is 'scanned' or 'failed'.  Failed entries have None for bytes/digests.
    '''
    results = []
    reserved_disk = 0
    reserved_mem = 0

    # pre-fetch layer sizes in parallel
    def _fetch_size(item):
        name, ref = item
        return name, ref, _compressed_layer_bytes(ref, oci_client)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        pending = list(executor.map(_fetch_size, items))

    def _can_admit(est_disk: int, est_mem: int, force: bool) -> bool:
        if force:
            return True
        avail_disk = _available_disk_bytes(tmpdir) - reserved_disk
        avail_mem = _available_mem_bytes() - reserved_mem
        return (
            avail_disk - est_disk >= _DISK_HEADROOM
            and avail_mem - est_mem >= _MEM_HEADROOM
        )

    def _do_scan(name, ref, est_disk, est_mem):
        try:
            spdx, cdx, cbom, ver, cbom_ver, spdx_dig, cdx_dig, cbom_dig = scan_image(
                image_ref=ref,
                oci_client=oci_client,
                tmpdir=tmpdir,
                tool_ver=tool_ver,
            )
            return name, spdx, cdx, cbom, ver, cbom_ver, spdx_dig, cdx_dig, cbom_dig, 'scanned'
        except Exception as e:
            logger.warning(f'{name!r}: scan failed: {e}')
            return name, None, None, None, None, None, None, None, None, 'failed'

    with concurrent.futures.ThreadPoolExecutor() as executor:
        running: dict[concurrent.futures.Future, tuple[int, int]] = {}

        while pending or running:
            while pending:
                name, ref, clb = pending[0]
                est_disk, est_mem = _estimate_bytes(clb)
                force = not running
                if not _can_admit(est_disk, est_mem, force):
                    break
                pending.pop(0)
                reserved_disk += est_disk
                reserved_mem += est_mem
                f = executor.submit(_do_scan, name, ref, est_disk, est_mem)
                running[f] = (est_disk, est_mem)
                logger.info(
                    f'admitted SBOM/CBOM scan for {name!r} '
                    f'(est disk={est_disk // 1024 // 1024} MB '
                    f'mem={est_mem // 1024 // 1024} MB, '
                    f'{len(running)} running, {len(pending)} pending)'
                )

            if not running:
                break

            done, _ = concurrent.futures.wait(
                running,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for f in done:
                est_disk, est_mem = running.pop(f)
                reserved_disk -= est_disk
                reserved_mem -= est_mem
                results.append(f.result())

    return results


def build_sbom_ocm_resources(
    resource_name: str,
    version: str,
    source_image_ref: str,
    source_digest: str,
    repo_ref: str,
    spdx_referrer_digest: str,
    cdx_referrer_digest: str,
    cbom_referrer_digest: str = '',
    tool_ver: str | None = None,
    cbom_tool_ver: str | None = None,
    source_extra_identity: dict | None = None,
) -> tuple[ocm.Resource, ocm.Resource, ocm.Resource]:
    '''
    Build (spdx_resource, cdx_resource, cbom_resource) OCM Resource objects.

    All three use OciAccess pointing at the referrer manifest digest already pushed to the
    target.  `source_extra_identity` is merged into each resource's extraIdentity so that
    resources derived from same-named sources with different extraIdentity (e.g. different
    platforms) remain distinguishable.
    '''
    def _make_sbom(media_type, sbom_format, referrer_digest):
        label_value = {
            'data-source': {
                'kind': 'local-scan',
                'tool': 'syft',
                'tool-version': tool_ver,
            },
            'format': sbom_format,
        } if tool_ver else None
        labels = [
            ocm.Label(name='gardener.cloud/sbom/source-image',        value=source_image_ref),
            ocm.Label(name='gardener.cloud/sbom/source-image-digest', value=source_digest),
        ]
        if label_value:
            labels.append(ocm.Label(name='gardener.cloud/sbom', value=label_value))
        extra_id = {
            **(source_extra_identity or {}),
            'version': version,
            'sbom-format': sbom_format,
        }
        return ocm.Resource(
            name=resource_name,
            version=version,
            type=media_type,
            relation=ocm.ResourceRelation.EXTERNAL,
            extraIdentity=extra_id,
            access=ocm.OciAccess(imageReference=f'{repo_ref}@{referrer_digest}'),
            labels=labels,
        )

    label_value = {
        'data-source': {
            'kind': 'local-scan',
            'tool': 'cbomkit-theia',
            'tool-version': cbom_tool_ver,
        },
        'format': 'cyclonedx-1.6+cbom',
    } if cbom_tool_ver else None
    cbom_labels = [
        ocm.Label(name='gardener.cloud/cbom/source-image',        value=source_image_ref),
        ocm.Label(name='gardener.cloud/cbom/source-image-digest', value=source_digest),
    ]
    if label_value:
        cbom_labels.append(ocm.Label(name='gardener.cloud/cbom', value=label_value))
    cbom_extra_id = {
        **(source_extra_identity or {}),
        'version': version,
        'cbom-format': 'cyclonedx-1.6+cbom',
    }
    cbom_resource = ocm.Resource(
        name=resource_name,
        version=version,
        type=scbom.CBOM_LAYER_MEDIA_TYPE,
        relation=ocm.ResourceRelation.EXTERNAL,
        extraIdentity=cbom_extra_id,
        access=ocm.OciAccess(imageReference=f'{repo_ref}@{cbom_referrer_digest}'),
        labels=cbom_labels,
    )

    return (
        _make_sbom(soci.SPDX_JSON_MEDIA_TYPE,     'spdx-2.3',      spdx_referrer_digest),
        _make_sbom(soci.CYCLONEDX_JSON_MEDIA_TYPE, 'cyclonedx-1.6', cdx_referrer_digest),
        cbom_resource,
    )


def _push_cbom_standalone(
    cbom_bytes: bytes,
    repo_ref: str,
    oci_client: oc.Client,
    tool_version: str | None = None,
) -> str:
    '''
    Push a CBOM document as a standalone OCI manifest (no subject). Returns manifest digest.
    '''
    doc_digest = f'sha256:{hashlib.sha256(cbom_bytes).hexdigest()}'
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=doc_digest,
        octets_count=len(cbom_bytes),
        data=cbom_bytes,
        mimetype=scbom.CBOM_LAYER_MEDIA_TYPE,
    )
    oci_client.put_blob(
        image_reference=repo_ref,
        digest=soci._EMPTY_CONFIG_DIGEST,
        octets_count=len(soci._EMPTY_CONFIG),
        data=soci._EMPTY_CONFIG,
        mimetype=soci.OCI_EMPTY_CONFIG_MEDIA_TYPE,
    )
    manifest = om.OciImageManifest(
        config=om.OciBlobRef(
            digest=soci._EMPTY_CONFIG_DIGEST,
            mediaType=soci.OCI_EMPTY_CONFIG_MEDIA_TYPE,
            size=len(soci._EMPTY_CONFIG),
        ),
        layers=[om.OciBlobRef(
            digest=doc_digest,
            mediaType=scbom.CBOM_LAYER_MEDIA_TYPE,
            size=len(cbom_bytes),
        )],
        artifactType=scbom.CBOM_ARTIFACT_TYPE,
        annotations={
            'org.opencontainers.image.created': soci._utcnow_iso(),
            **({'gardener.cloud/cbom/tool-version': tool_version} if tool_version else {}),
        },
    )
    manifest_bytes = json.dumps(manifest.as_dict()).encode()
    manifest_digest = f'sha256:{hashlib.sha256(manifest_bytes).hexdigest()}'
    oci_client.put_manifest(
        image_reference=f'{repo_ref}@{manifest_digest}',
        manifest=manifest_bytes,
    )
    return manifest_digest


def scan_s3_resource(
    access: 'ocm.S3Access | ocm.LegacyS3Access',
    oci_client: oc.Client,
    registry_base: str,
    tmpdir: str,
    tool_ver: str | None = None,
) -> tuple[bytes, bytes, bytes, str | None, str | None, str, str, str, str, str]:
    '''
    Scan an S3-backed OCM resource with syft + cbomkit-theia.

    Downloads the object from the public S3 bucket, runs syft on the local file, then runs
    cbomkit-theia on the CycloneDX output.  All three SBOM/CBOM documents are pushed as
    standalone OCI manifests (no subject) to a content-addressed synthetic OCI ref under
    `registry_base/sbom-s3/...`.

    Returns:
      (spdx_bytes, cdx_bytes, cbom_bytes,
       tool_ver, cbom_tool_ver,
       spdx_manifest_digest, cdx_manifest_digest, cbom_manifest_digest,
       repo_ref, content_digest)

    where:
      content_digest  = sha256 of the downloaded S3 object (used for OCM resource labels)
      repo_ref        = the synthetic OCI repo path (without digest/tag)
    '''
    if isinstance(access, ocm.LegacyS3Access):
        bucket, key, region = access.bucketName, access.objectKey, access.region
    else:
        bucket, key, region = access.bucket, access.key, access.region

    logger.info(f'downloading S3 object s3://{bucket}/{key}')

    env = os.environ.copy()
    env['TMPDIR'] = tmpdir

    with tempfile.TemporaryDirectory(dir=tmpdir) as tmp:
        blob_path = os.path.join(tmp, 'blob')
        h = hashlib.sha256()
        size = 0
        with open(blob_path, 'wb') as f:
            for chunk in ss3.iter_s3_object(bucket=bucket, key=key, region=region):
                h.update(chunk)
                f.write(chunk)
                size += len(chunk)
        content_digest = f'sha256:{h.hexdigest()}'
        logger.info(f's3://{bucket}/{key}: {size} bytes, {content_digest}')

        full_synthetic = ss3.synthetic_oci_ref(registry_base, bucket, key, content_digest)
        repo_ref = om.OciImageReference(full_synthetic).ref_without_tag

        spdx_path = os.path.join(tmp, 'sbom.spdx.json')
        cdx_path  = os.path.join(tmp, 'sbom.cdx.json')
        cbom_path = os.path.join(tmp, 'cbom.cdx.json')

        subprocess.run(  # nosec B607
            [
                'syft', 'scan', blob_path,
                '-o', f'spdx-json={spdx_path}',
                '-o', f'cyclonedx-json@1.6={cdx_path}',
            ],
            check=True,
            env=env,
        )

        with open(spdx_path, 'rb') as f:
            spdx_bytes = f.read()
        with open(cdx_path, 'rb') as f:
            cdx_bytes = f.read()

        _run_cbomkit_theia(
            image_ref=blob_path,  # cbomkit-theia accepts local paths too
            cdx_bom_path=cdx_path,
            out_path=cbom_path,
            tmpdir=tmp,
        )
        with open(cbom_path, 'rb') as f:
            cbom_bytes = f.read()

    resolved_tool_ver = tool_ver or _syft_version_from_spdx(spdx_bytes)
    cbom_tool_ver = _cbomkit_theia_version()

    spdx_digest, cdx_digest = soci.push_sbom_standalone(
        spdx_bytes=spdx_bytes,
        cdx_bytes=cdx_bytes,
        repo_ref=repo_ref,
        content_digest=content_digest,
        oci_client=oci_client,
        tool_version=resolved_tool_ver,
    )
    cbom_digest = _push_cbom_standalone(
        cbom_bytes=cbom_bytes,
        repo_ref=repo_ref,
        oci_client=oci_client,
        tool_version=cbom_tool_ver,
    )

    return (
        spdx_bytes, cdx_bytes, cbom_bytes,
        resolved_tool_ver, cbom_tool_ver,
        spdx_digest, cdx_digest, cbom_digest,
        repo_ref, content_digest,
    )


def lookup_s3_sboms(
    access: 'ocm.S3Access | ocm.LegacyS3Access',
    oci_client: oc.Client,
    registry_base: str,
    tmpdir: str,
) -> tuple[bytes, bytes, str, str, str, str] | None:
    '''
    Check whether SBOM standalone manifests already exist for an S3 resource.

    Always returns None for now: manifest digests are not pre-computable without first running
    the syft scan, so a true cache-hit path cannot be implemented without storing a separate
    digest index.  Scan + push operations are idempotent, so redundant runs are safe.

    A future optimisation could store manifest digests in a local cache file keyed by the
    S3 content digest (sha256 of the downloaded object).
    '''
    return None


def build_s3_sbom_ocm_resources(
    resource_name: str,
    version: str,
    s3_url: str,
    content_digest: str,
    repo_ref: str,
    spdx_manifest_digest: str,
    cdx_manifest_digest: str,
    cbom_manifest_digest: str,
    tool_ver: str | None = None,
    cbom_tool_ver: str | None = None,
    source_extra_identity: dict | None = None,
) -> tuple[ocm.Resource, ocm.Resource, ocm.Resource]:
    '''
    Build (spdx_resource, cdx_resource, cbom_resource) OCM Resource objects for an S3 resource.

    All three use OciAccess pointing at standalone manifests pushed to the synthetic OCI repo.
    `source_extra_identity` is merged into each resource's extraIdentity.
    '''
    def _make_sbom(media_type, sbom_format, manifest_digest):
        label_value = {
            'data-source': {
                'kind': 'local-scan',
                'tool': 'syft',
                'tool-version': tool_ver,
            },
            'format': sbom_format,
        } if tool_ver else None
        labels = [
            ocm.Label(name='gardener.cloud/sbom/source-image',        value=s3_url),
            ocm.Label(name='gardener.cloud/sbom/source-image-digest', value=content_digest),
        ]
        if label_value:
            labels.append(ocm.Label(name='gardener.cloud/sbom', value=label_value))
        extra_id = {
            **(source_extra_identity or {}),
            'version': version,
            'sbom-format': sbom_format,
        }
        return ocm.Resource(
            name=resource_name,
            version=version,
            type=media_type,
            relation=ocm.ResourceRelation.EXTERNAL,
            extraIdentity=extra_id,
            access=ocm.OciAccess(imageReference=f'{repo_ref}@{manifest_digest}'),
            labels=labels,
        )

    label_value = {
        'data-source': {
            'kind': 'local-scan',
            'tool': 'cbomkit-theia',
            'tool-version': cbom_tool_ver,
        },
        'format': 'cyclonedx-1.6+cbom',
    } if cbom_tool_ver else None
    cbom_labels = [
        ocm.Label(name='gardener.cloud/cbom/source-image',        value=s3_url),
        ocm.Label(name='gardener.cloud/cbom/source-image-digest', value=content_digest),
    ]
    if label_value:
        cbom_labels.append(ocm.Label(name='gardener.cloud/cbom', value=label_value))
    cbom_extra_id = {
        **(source_extra_identity or {}),
        'version': version,
        'cbom-format': 'cyclonedx-1.6+cbom',
    }
    cbom_resource = ocm.Resource(
        name=resource_name,
        version=version,
        type=scbom.CBOM_LAYER_MEDIA_TYPE,
        relation=ocm.ResourceRelation.EXTERNAL,
        extraIdentity=cbom_extra_id,
        access=ocm.OciAccess(imageReference=f'{repo_ref}@{cbom_manifest_digest}'),
        labels=cbom_labels,
    )

    return (
        _make_sbom(soci.SPDX_JSON_MEDIA_TYPE,     'spdx-2.3',      spdx_manifest_digest),
        _make_sbom(soci.CYCLONEDX_JSON_MEDIA_TYPE, 'cyclonedx-1.6', cdx_manifest_digest),
        cbom_resource,
    )
