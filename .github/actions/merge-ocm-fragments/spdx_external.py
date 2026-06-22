#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Generate and cache SPDX, CycloneDX SBOM, and CycloneDX CBOM documents for external OCM
OCI-image resources.

Processing is split into two phases:

  Phase 1 (parallel, unconstrained):
    For each external OCI image: resolve the platform-specific digest, fetch the manifest
    to obtain compressed layer sizes, and check the SBOM/CBOM cache.  Produces a list of
    full hits (all three documents cached), partial hits (SBOM cached but CBOM missing), and
    full misses (syft scan required).

  Phase 2a (parallel, unconstrained):
    Download cached blobs for all full hits; for partial hits, download SBOM blobs and run
    cbomkit-theia only.

  Phase 2b (resource-aware sequential/parallel):
    Run syft + cbomkit-theia for full misses.  Before admitting each scan, check available
    memory and tmpfs space.  At least one scan is always admitted to prevent deadlock.

Cache addressing:
    <cache_registry>/<cache_repo_prefix>/<mangled-source-repo>:<cache-tag>

  <mangled-source-repo>  source image repository path with '/' and ':' replaced by '-'
  <cache-tag>            source image digest with ':' replaced by '-' (e.g. sha256-abc123…)

  Cache manifest layers:
    layer[0] = SPDX JSON
    layer[1] = CycloneDX SBOM JSON
    layer[2] = CycloneDX CBOM JSON  (absent in legacy 2-layer entries → CBOM partial miss)

TMPDIR is forwarded to syft and cbomkit-theia subprocesses.

Resource estimation (per syft invocation):
  disk:   compressed_layer_bytes * 5.0
  memory: 200 MiB + compressed_layer_bytes * 2.0

These factors are derived from empirical measurement on alpine:3
(~3.5 MB compressed → ~17 MB tmpfs, ~170 MB RSS).
Minimum headroom kept free: 2 GiB disk, 1 GiB memory.
'''
import concurrent.futures
import dataclasses
import enum
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap

import yaml

import oci.auth as oa
import oci.client as oc
import oci.model as om
import sbom.cbom as scbom
import sbom.oci as osbom
import ocm

logger = logging.getLogger(__name__)

# Canonical document types we want to produce for every image, in cache-layer order.
# Each entry: (format_id, media_type, layer_index)
# This table drives both cache interpretation and scan scheduling.
_WANTED_DOCS: tuple[tuple[str, str, int], ...] = (
    ('spdx-2.3',          osbom.SPDX_JSON_MEDIA_TYPE,       0),
    ('cyclonedx-1.6',     osbom.CYCLONEDX_JSON_MEDIA_TYPE,  1),
    ('cbom-cyclonedx-1.6', scbom.CBOM_LAYER_MEDIA_TYPE,     2),
)


@dataclasses.dataclass
class _ImageInfo:
    resource: dict
    # digest-addressed ref of the platform-specific manifest (what we pass to syft)
    digest_ref: str
    # sha256:<hex> of the platform manifest — used as cache key
    source_digest: str
    # sum of compressed layer sizes in bytes — used for resource estimation
    compressed_layer_bytes: int


@dataclasses.dataclass
class _CacheResult:
    info: _ImageInfo
    # format_id → blob bytes for documents present in the cache (subset of _WANTED_DOCS)
    cached: dict[str, bytes] = dataclasses.field(default_factory=dict)

    @property
    def missing(self) -> tuple[str, ...]:
        '''format_ids that are wanted but absent from the cache.'''
        return tuple(fid for fid, _, _ in _WANTED_DOCS if fid not in self.cached)


class _ScanStatus(enum.Enum):
    RESOLVE_FAILED  = 'resolve-failed'
    CACHE_HIT       = 'cache-hit'
    SCAN_CBOM_ONLY  = 'scan-cbom-only'
    SCANNED         = 'scanned'
    SCAN_FAILED     = 'scan-failed'


@dataclasses.dataclass
class _ScanRecord:
    resource_name: str
    image_ref: str
    status: _ScanStatus
    compressed_mb: float = 0.0  # 0 if resolution failed


def _mangle(repo: str) -> str:
    return repo.replace('/', '-').replace(':', '-')


def _cache_repo(cache_registry: str, prefix: str, source_ref: om.OciImageReference) -> str:
    return f'{cache_registry}/{prefix}/{_mangle(source_ref.ref_without_tag)}'


def _cache_tag(source_digest: str) -> str:
    # OCI tags cannot contain ':', so encode sha256:<hex> as sha256-<hex>
    return source_digest.replace(':', '-', 1)


def _cache_ref(cache_registry: str, prefix: str, source_ref: om.OciImageReference,
               source_digest: str) -> str:
    return f'{_cache_repo(cache_registry, prefix, source_ref)}:{_cache_tag(source_digest)}'


def _available_disk_bytes(path: str) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def _available_mem_bytes() -> int:
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    return 0


def _estimate_bytes(info: _ImageInfo) -> tuple[int, int]:
    '''return (estimated_disk_bytes, estimated_mem_bytes) for one syft invocation.'''
    c = info.compressed_layer_bytes
    return (
        int(c * 5.0),                                   # empirical: alpine:3 → ~5× tmpfs expansion
        int(200 * 1024 * 1024 + c * 2.0),              # 200 MiB base + ~2× RSS per layer byte
    )


def _resolve_image_info(
    resource: dict,
    oci_client: oc.Client,
) -> _ImageInfo | None:
    '''
    Resolve the platform-specific manifest digest and compressed layer sizes for one resource.
    For image indexes, picks the linux/amd64 entry; falls back to the first entry.
    Returns None on error (logs a warning).
    '''
    image_ref_str: str = resource['access']['imageReference']
    resource_name: str = resource['name']

    source_ref = om.OciImageReference.to_image_ref(image_ref_str)
    try:
        manifest = oci_client.manifest(
            source_ref,
            accept=om.MimeTypes.prefer_multiarch,
        )
    except Exception as e:
        logger.warning(f'{resource_name!r}: failed to fetch manifest for {image_ref_str}: {e}')
        return None

    if isinstance(manifest, om.OciImageManifestList):
        entry = _pick_platform(manifest, resource_name)
        if entry is None:
            return None
        platform_ref = f'{source_ref.ref_without_tag}@{entry.digest}'
        try:
            manifest = oci_client.manifest(platform_ref)
        except Exception as e:
            logger.warning(f'{resource_name!r}: failed to fetch platform manifest: {e}')
            return None
        digest_ref = platform_ref
        source_digest = entry.digest
    else:
        # already a single-image manifest; compute digest from raw bytes
        try:
            raw = oci_client.manifest_raw(source_ref).content
        except Exception as e:
            logger.warning(f'{resource_name!r}: failed to fetch raw manifest: {e}')
            return None
        source_digest = f'sha256:{hashlib.sha256(raw).hexdigest()}'
        digest_ref = f'{source_ref.ref_without_tag}@{source_digest}'

    compressed_layer_bytes = sum(layer.size for layer in manifest.layers)

    return _ImageInfo(
        resource=resource,
        digest_ref=digest_ref,
        source_digest=source_digest,
        compressed_layer_bytes=compressed_layer_bytes,
    )


def _pick_platform(
    manifest_list: om.OciImageManifestList,
    resource_name: str,
    preferred_os: str = 'linux',
    preferred_arch: str = 'amd64',
) -> om.OciImageManifestListEntry | None:
    entries = [
        e for e in manifest_list.manifests
        if e.platform and e.platform.os == preferred_os
        and e.platform.architecture == preferred_arch
    ]
    if entries:
        return entries[0]
    # fall back to first entry with a platform
    entries = [e for e in manifest_list.manifests if e.platform]
    if entries:
        logger.warning(
            f'{resource_name!r}: no linux/amd64 entry found, '
            f'falling back to {entries[0].platform.os}/{entries[0].platform.architecture}'
        )
        return entries[0]
    logger.warning(f'{resource_name!r}: manifest list has no entries with platform info')
    return None


def _check_cache(
    info: _ImageInfo,
    oci_client: oc.Client,
    cache_registry: str,
    cache_prefix: str,
) -> _CacheResult:
    '''
    Check the cache for all wanted document types.

    Returns a _CacheResult with `cached` populated for each format_id that has a layer
    entry in the cache manifest.  An absent/unreachable cache entry → empty `cached`.
    '''
    source_ref = om.OciImageReference.to_image_ref(info.digest_ref)
    cref = _cache_ref(cache_registry, cache_prefix, source_ref, info.source_digest)
    cache_repo = _cache_repo(cache_registry, cache_prefix, source_ref)
    result = _CacheResult(info=info)

    res = oci_client.manifest_raw(cref, absent_ok=True)
    if res is None:
        logger.info(f'{info.resource["name"]!r}: cache miss — full scan required')
        return result

    manifest = res.json()
    layers = manifest.get('layers', [])

    for fid, _, idx in _WANTED_DOCS:
        if idx >= len(layers):
            continue
        layer_digest = layers[idx]['digest']
        try:
            blob = oci_client.blob(image_reference=cache_repo, digest=layer_digest).content
            result.cached[fid] = blob
        except Exception as e:
            logger.warning(
                f'{info.resource["name"]!r}: failed to fetch cached {fid} blob: {e}'
            )

    missing = result.missing
    if missing:
        logger.info(
            f'{info.resource["name"]!r}: partial cache hit — missing: {missing}'
        )
    else:
        logger.info(f'{info.resource["name"]!r}: full cache hit')
    return result


def _run_syft(image_reference: str, spdx_out_path: str, cdx_out_path: str, tmpdir: str):
    env = os.environ.copy()
    env['TMPDIR'] = tmpdir
    subprocess.run(  # nosec B607
        [
            'syft', 'scan', image_reference,
            '-o', f'spdx-json={spdx_out_path}',
            '-o', f'cyclonedx-json@1.6={cdx_out_path}',
        ],
        check=True,
        env=env,
    )


def _run_cbomkit_theia(
    image_reference: str,
    cdx_bom_path: str,
    cbom_out_path: str,
    tmpdir: str,
) -> None:
    env = os.environ.copy()
    env['TMPDIR'] = tmpdir
    with open(cbom_out_path, 'w') as out_f:
        subprocess.run(  # nosec B607
            [
                'cbomkit-theia', 'image',
                '--bom', cdx_bom_path,
                image_reference,
            ],
            check=True,
            stdout=out_f,
            env=env,
        )


def _syft_version_from_spdx(spdx_bytes: bytes) -> str | None:
    '''Extract the syft version from a SPDX JSON document's creationInfo.creators field.'''
    try:
        doc = json.loads(spdx_bytes)
        for creator in doc.get('creationInfo', {}).get('creators', []):
            if creator.startswith('Tool: syft-'):
                return creator[len('Tool: syft-'):]
    except Exception:  # nosec B110
        pass
    return None


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


def _push_sboms_to_cache(
    spdx_path: str,
    cdx_path: str,
    cbom_path: str | None,
    cache_repo: str,
    source_digest: str,
    oci_client: oc.Client,
    tool_version: str | None,
) -> None:
    '''
    Push SPDX and CycloneDX SBOM blobs (and optionally CBOM) as an OCI manifest into the
    cache repo, addressed by a tag derived from source_digest.

    layer[0] = SPDX JSON, layer[1] = CycloneDX SBOM JSON[, layer[2] = CycloneDX CBOM JSON].
    '''
    with open(spdx_path, 'rb') as f:
        spdx_bytes = f.read()
    with open(cdx_path, 'rb') as f:
        cdx_bytes = f.read()
    cbom_bytes = None
    if cbom_path is not None:
        with open(cbom_path, 'rb') as f:
            cbom_bytes = f.read()

    empty_config = b'{}'
    empty_config_digest = f'sha256:{hashlib.sha256(empty_config).hexdigest()}'
    oci_client.put_blob(
        image_reference=cache_repo,
        digest=empty_config_digest,
        octets_count=len(empty_config),
        data=empty_config,
        mimetype=osbom.OCI_EMPTY_CONFIG_MEDIA_TYPE,
    )

    layer_specs = [
        (spdx_bytes, osbom.SPDX_JSON_MEDIA_TYPE),
        (cdx_bytes, osbom.CYCLONEDX_JSON_MEDIA_TYPE),
    ]
    if cbom_bytes is not None:
        layer_specs.append((cbom_bytes, scbom.CBOM_LAYER_MEDIA_TYPE))

    layers = []
    for data, media_type in layer_specs:
        digest = f'sha256:{hashlib.sha256(data).hexdigest()}'
        oci_client.put_blob(
            image_reference=cache_repo,
            digest=digest,
            octets_count=len(data),
            data=data,
            mimetype=media_type,
        )
        layers.append(om.OciBlobRef(digest=digest, mediaType=media_type, size=len(data)))

    manifest = om.OciImageManifest(
        config=om.OciBlobRef(
            digest=empty_config_digest,
            mediaType=osbom.OCI_EMPTY_CONFIG_MEDIA_TYPE,
            size=len(empty_config),
        ),
        layers=layers,
        annotations={
            **({'gardener.cloud/sbom/tool-version': tool_version} if tool_version else {}),
        },
    )

    manifest_bytes = json.dumps(manifest.as_dict()).encode()
    cache_ref = f'{cache_repo}:{_cache_tag(source_digest)}'
    oci_client.put_manifest(
        image_reference=cache_ref,
        manifest=manifest_bytes,
    )


def _scan_image(
    miss: _CacheResult,
    oci_client: oc.Client,
    cache_registry: str,
    cache_prefix: str,
    syft_ver: str | None,
    tmpdir: str,
) -> _CacheResult | None:
    '''
    Produce all missing documents for `miss` by running the appropriate tools,
    update the cache, and return a fully-populated _CacheResult.

    - If SPDX or CycloneDX SBOM is missing: run syft (produces both SBOM formats at once).
    - If only CBOM is missing (SBOM already cached): run cbomkit-theia only.

    Returns None on complete failure.
    '''
    info = miss.info
    resource_name = info.resource['name']
    source_ref = om.OciImageReference.to_image_ref(info.digest_ref)
    cache_repo = _cache_repo(cache_registry, cache_prefix, source_ref)
    missing = miss.missing
    cached = dict(miss.cached)  # copy; we'll populate it

    need_syft    = 'spdx-2.3' in missing or 'cyclonedx-1.6' in missing
    need_cbomkit = 'cbom-cyclonedx-1.6' in missing

    with tempfile.TemporaryDirectory(dir=tmpdir) as tmp:
        spdx_path = os.path.join(tmp, 'sbom.spdx.json')
        cdx_path  = os.path.join(tmp, 'sbom.cdx.json')
        cbom_path = os.path.join(tmp, 'cbom.cdx.json')

        if need_syft:
            try:
                logger.info(f'{resource_name!r}: running syft on {info.digest_ref}')
                _run_syft(info.digest_ref, spdx_path, cdx_path, tmpdir)
                with open(spdx_path, 'rb') as f:
                    cached['spdx-2.3'] = f.read()
                with open(cdx_path, 'rb') as f:
                    cached['cyclonedx-1.6'] = f.read()
            except Exception as e:
                logger.warning(f'{resource_name!r}: syft failed: {e}')
                return None
        else:
            # write cached CDX to disk so cbomkit-theia can use it as --bom input
            with open(cdx_path, 'wb') as f:
                f.write(cached['cyclonedx-1.6'])

        if need_cbomkit:
            try:
                logger.info(f'{resource_name!r}: running cbomkit-theia on {info.digest_ref}')
                _run_cbomkit_theia(info.digest_ref, cdx_path, cbom_path, tmpdir)
                with open(cbom_path, 'rb') as f:
                    cached['cbom-cyclonedx-1.6'] = f.read()
            except Exception as e:
                logger.warning(f'{resource_name!r}: cbomkit-theia failed: {e}')
                # if syft already ran and produced SBOM, cache those before giving up
                spdx_done = cached.get('spdx-2.3', b'')
                cdx_done  = cached.get('cyclonedx-1.6', b'')
                if spdx_done and cdx_done:
                    if need_syft:
                        # paths already on disk from syft run above
                        pass
                    else:
                        with open(spdx_path, 'wb') as f:
                            f.write(spdx_done)
                    # cbom_path not written — push 2-layer cache entry
                    logger.info(
                        f'{resource_name!r}: caching SBOM-only (no CBOM) at '
                        f'{cache_repo}:{_cache_tag(info.source_digest)}'
                    )
                    _push_sboms_to_cache(
                        spdx_path=spdx_path,
                        cdx_path=cdx_path,
                        cbom_path=None,
                        cache_repo=cache_repo,
                        source_digest=info.source_digest,
                        oci_client=oci_client,
                        tool_version=syft_ver,
                    )
                return None

        # update cache with all three layers
        spdx_final = cached.get('spdx-2.3', b'')
        cdx_final  = cached.get('cyclonedx-1.6', b'')
        cbom_final = cached.get('cbom-cyclonedx-1.6', b'')
        if spdx_final and cdx_final and cbom_final:
            # write out paths for _push_sboms_to_cache (expects file paths)
            if not need_syft:
                with open(spdx_path, 'wb') as f:
                    f.write(spdx_final)
            if not need_cbomkit:
                with open(cbom_path, 'wb') as f:
                    f.write(cbom_final)
            logger.info(
                f'{resource_name!r}: pushing SBOM+CBOM to cache '
                f'{cache_repo}:{_cache_tag(info.source_digest)}'
            )
            _push_sboms_to_cache(
                spdx_path=spdx_path,
                cdx_path=cdx_path,
                cbom_path=cbom_path,
                cache_repo=cache_repo,
                source_digest=info.source_digest,
                oci_client=oci_client,
                tool_version=syft_ver,
            )

    return _CacheResult(info=info, cached=cached)


def _store_blob(blobs_dir: str, data: bytes) -> str:
    digest = f'sha256:{hashlib.sha256(data).hexdigest()}'
    dest = os.path.join(blobs_dir, digest)
    if not os.path.exists(dest):
        with open(dest, 'wb') as f:
            f.write(data)
    return digest


def _build_sbom_resources(
    info: _ImageInfo,
    spdx_bytes: bytes,
    cdx_bytes: bytes,
    cbom_bytes: bytes | None,
    spdx_blob_digest: str,
    cdx_blob_digest: str,
    cbom_blob_digest: str | None,
    component_version: str,
    tool_ver: str | None,
) -> tuple[dict, ...]:
    '''Return (spdx_resource, cdx_resource[, cbom_resource]) OCM resource dicts.'''
    resource = info.resource
    name = resource['name']
    version = resource.get('version') or component_version
    source_image_ref = resource['access']['imageReference']

    sbom_resources = osbom.build_sbom_ocm_resources(
        resource_name=name,
        version=version,
        source_image_ref=source_image_ref,
        source_digest=info.source_digest,
        spdx_bytes=spdx_bytes,
        cdx_bytes=cdx_bytes,
        spdx_blob_digest=spdx_blob_digest,
        cdx_blob_digest=cdx_blob_digest,
        tool_ver=tool_ver,
    )

    if cbom_bytes is None or cbom_blob_digest is None:
        return sbom_resources

    cbom_resources = scbom.build_cbom_ocm_resources(
        resource_name=name,
        version=version,
        source_image_ref=source_image_ref,
        source_digest=info.source_digest,
        cbom_bytes=cbom_bytes,
        cbom_blob_digest=cbom_blob_digest,
        tool_ver=None,
    )
    return (*sbom_resources, *cbom_resources)


def _run_scans_resource_aware(
    incomplete: list[_CacheResult],
    oci_client: oc.Client,
    cache_registry: str,
    cache_prefix: str,
    syft_ver: str | None,
    tmpdir: str,
) -> list[_CacheResult]:
    '''
    Admit _scan_image calls one at a time, gated by available memory and tmpfs space.
    `incomplete` contains _CacheResult entries with at least one missing document.
    Always admits at least one job to prevent deadlock.
    '''
    results = []
    pending = list(incomplete)
    reserved_disk = 0
    reserved_mem = 0

    def _can_admit(est_disk: int, est_mem: int, force: bool) -> bool:
        if force:
            return True
        avail_disk = _available_disk_bytes(tmpdir) - reserved_disk
        avail_mem = _available_mem_bytes() - reserved_mem
        return (
            avail_disk - est_disk >= 2 * 1024 * 1024 * 1024    # keep 2 GiB free
            and avail_mem - est_mem >= 1 * 1024 * 1024 * 1024  # keep 1 GiB free
        )

    with concurrent.futures.ThreadPoolExecutor() as executor:
        running: dict[concurrent.futures.Future, tuple[int, int]] = {}

        while pending or running:
            while pending:
                cr = pending[0]
                est_disk, est_mem = _estimate_bytes(cr.info)
                force = not running
                if not _can_admit(est_disk, est_mem, force):
                    break
                pending.pop(0)
                reserved_disk += est_disk
                reserved_mem += est_mem
                f = executor.submit(
                    _scan_image,
                    cr,
                    oci_client,
                    cache_registry,
                    cache_prefix,
                    syft_ver,
                    tmpdir,
                )
                running[f] = (est_disk, est_mem)
                logger.info(
                    f'admitted scan for {cr.info.resource["name"]!r} '
                    f'(missing={cr.missing}, '
                    f'est disk={est_disk//1024//1024}MB '
                    f'mem={est_mem//1024//1024}MB, '
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
                result = f.result()
                if result is not None:
                    results.append(result)

    return results


def _write_step_summary(records: list[_ScanRecord]) -> None:
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_path:
        return

    hits       = [r for r in records if r.status is _ScanStatus.CACHE_HIT]
    scanned    = [r for r in records if r.status is _ScanStatus.SCANNED]
    cbom_only  = [r for r in records if r.status is _ScanStatus.SCAN_CBOM_ONLY]
    failed     = [r for r in records if r.status in (
        _ScanStatus.SCAN_FAILED, _ScanStatus.RESOLVE_FAILED,
    )]

    rows = '\n'.join(
        f'| `{r.resource_name}` | `{r.image_ref}` '
        f'| {r.compressed_mb:.1f} MB | {r.status.value} |'
        for r in records
    )

    summary = textwrap.dedent(f'''\
        ## SBOM + CBOM — external OCI images

        | | count |
        |---|---|
        | full cache hits | {len(hits)} |
        | freshly scanned (SBOM+CBOM) | {len(scanned)} |
        | CBOM-only scan (SBOM cached) | {len(cbom_only)} |
        | failed | {len(failed)} |
        | **total** | **{len(records)}** |

        | resource | image | compressed size | status |
        |---|---|---|---|
        {rows}
    ''')

    with open(summary_path, 'a') as f:
        f.write(summary)


def process_external_resources(
    component_descriptor_path: str,
    out_dir: str,
    cache_registry: str,
    cache_repo_prefix: str = 'sbom-cache',
    force_rescan: bool = False,
) -> None:
    '''
    Scan external OCI image resources for SPDX SBOM, CycloneDX SBOM, and CycloneDX CBOM
    documents; patch results into the component descriptor.
    '''
    tmpdir = os.environ.get('RUNNER_TEMP') or os.environ.get('TMPDIR') or tempfile.gettempdir()
    logger.info(f'using tmpdir for syft/cbomkit-theia: {tmpdir}')

    oci_client = oc.Client(credentials_lookup=oa.docker_credentials_lookup())

    with open(component_descriptor_path) as f:
        cd_raw = yaml.safe_load(f)

    component = cd_raw['component']
    component_version: str = component.get('version', '')
    resources: list[dict] = component.get('resources', [])

    def _is_oci_registry_access(access: dict) -> bool:
        try:
            return ocm.AccessType(access.get('type', '')) is ocm.AccessType.OCI_REGISTRY
        except ValueError:
            return False

    def _is_oci_image(resource: dict) -> bool:
        try:
            return ocm.ArtefactType(resource.get('type', '')) is ocm.ArtefactType.OCI_IMAGE
        except ValueError:
            return False

    external_oci = [
        r for r in resources
        if r.get('relation') == ocm.ResourceRelation.EXTERNAL
        and _is_oci_image(r)
        and isinstance(r.get('access'), dict)
        and _is_oci_registry_access(r['access'])
    ]

    if not external_oci:
        logger.info('no external OCI image resources found — nothing to do')
        return

    logger.info(f'found {len(external_oci)} external OCI image resource(s)')

    # --- phase 1: parallel digest resolution, manifest fetch, cache check ---
    logger.info('phase 1: resolving manifests and checking cache')

    with concurrent.futures.ThreadPoolExecutor() as executor:
        image_infos = list(executor.map(
            lambda r: _resolve_image_info(r, oci_client),
            external_oci,
        ))

    records: list[_ScanRecord] = []
    resolved: list[_ImageInfo] = []
    for resource, info in zip(external_oci, image_infos):
        if info is None:
            records.append(_ScanRecord(
                resource_name=resource['name'],
                image_ref=resource['access']['imageReference'],
                status=_ScanStatus.RESOLVE_FAILED,
            ))
        else:
            resolved.append(info)

    if force_rescan:
        logger.info('force-rescan: skipping cache check')
        cache_results = [_CacheResult(info=i) for i in resolved]
    else:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            cache_results = list(executor.map(
                lambda i: _check_cache(i, oci_client, cache_registry, cache_repo_prefix),
                resolved,
            ))

    full_hits  = [cr for cr in cache_results if not cr.missing]
    incomplete = [cr for cr in cache_results if cr.missing]
    # record each image's missing set now, before _scan_image returns a fully-populated result
    original_missing: dict[str, tuple[str, ...]] = {
        cr.info.resource['name']: cr.missing for cr in incomplete
    }
    logger.info(
        f'phase 1 done: {len(full_hits)} full cache hit(s), '
        f'{len(incomplete)} incomplete (need scan)'
    )

    # --- phase 2: resource-aware scanning for incomplete entries ---
    syft_ver = _syft_version()
    all_results: list[_CacheResult] = list(full_hits)

    if incomplete:
        # CBOM-only partial hits can be done in parallel (no syft, so cheaper)
        cbom_only_list = [
            cr for cr in incomplete
            if cr.missing == ('cbom-cyclonedx-1.6',)
        ]
        need_syft_list = [
            cr for cr in incomplete
            if cr.missing != ('cbom-cyclonedx-1.6',)
        ]

        if cbom_only_list:
            logger.info(
                f'phase 2: {len(cbom_only_list)} CBOM-only scan(s) (SBOM already cached)'
            )
            all_results.extend(_run_scans_resource_aware(
                incomplete=cbom_only_list,
                oci_client=oci_client,
                cache_registry=cache_registry,
                cache_prefix=cache_repo_prefix,
                syft_ver=syft_ver,
                tmpdir=tmpdir,
            ))
            scanned_cbom_names = {r.info.resource['name'] for r in all_results}
            for cr in cbom_only_list:
                if cr.info.resource['name'] not in scanned_cbom_names:
                    records.append(_ScanRecord(
                        resource_name=cr.info.resource['name'],
                        image_ref=cr.info.resource['access']['imageReference'],
                        status=_ScanStatus.SCAN_FAILED,
                        compressed_mb=cr.info.compressed_layer_bytes / 1024 / 1024,
                    ))

        if need_syft_list:
            logger.info(
                f'phase 2: {len(need_syft_list)} full scan(s) '
                f'with syft {syft_ver or "?"} + cbomkit-theia'
            )
            all_results.extend(_run_scans_resource_aware(
                incomplete=need_syft_list,
                oci_client=oci_client,
                cache_registry=cache_registry,
                cache_prefix=cache_repo_prefix,
                syft_ver=syft_ver,
                tmpdir=tmpdir,
            ))
            scanned_names = {r.info.resource['name'] for r in all_results}
            for cr in need_syft_list:
                if cr.info.resource['name'] not in scanned_names:
                    records.append(_ScanRecord(
                        resource_name=cr.info.resource['name'],
                        image_ref=cr.info.resource['access']['imageReference'],
                        status=_ScanStatus.SCAN_FAILED,
                        compressed_mb=cr.info.compressed_layer_bytes / 1024 / 1024,
                    ))

    if not all_results:
        logger.info('no BOM results to add to component descriptor')
        _write_step_summary(records)
        return

    blobs_dir = os.path.join(out_dir, 'blobs.d')
    os.makedirs(blobs_dir, exist_ok=True)

    new_resources = []
    for cr in all_results:
        info = cr.info
        spdx_bytes  = cr.cached.get('spdx-2.3')
        cdx_bytes   = cr.cached.get('cyclonedx-1.6')
        cbom_bytes  = cr.cached.get('cbom-cyclonedx-1.6')

        if not spdx_bytes or not cdx_bytes:
            logger.warning(f'{info.resource["name"]!r}: SBOM missing from results — skipping')
            continue

        spdx_digest = _store_blob(blobs_dir, spdx_bytes)
        cdx_digest  = _store_blob(blobs_dir, cdx_bytes)
        cbom_digest = _store_blob(blobs_dir, cbom_bytes) if cbom_bytes else None
        tool_ver = _syft_version_from_spdx(spdx_bytes)

        bom_resources = _build_sbom_resources(
            info=info,
            spdx_bytes=spdx_bytes,
            cdx_bytes=cdx_bytes,
            cbom_bytes=cbom_bytes,
            spdx_blob_digest=spdx_digest,
            cdx_blob_digest=cdx_digest,
            cbom_blob_digest=cbom_digest,
            component_version=component_version,
            tool_ver=tool_ver,
        )
        new_resources.extend(bom_resources)

        was_full_hit = info.resource['name'] not in original_missing and not force_rescan
        orig_miss = original_missing.get(info.resource['name'], ())
        had_cbom_only_miss = cbom_bytes is not None and orig_miss == ('cbom-cyclonedx-1.6',)
        if was_full_hit:
            status = _ScanStatus.CACHE_HIT
        elif had_cbom_only_miss:
            status = _ScanStatus.SCAN_CBOM_ONLY
        else:
            status = _ScanStatus.SCANNED

        records.append(_ScanRecord(
            resource_name=info.resource['name'],
            image_ref=info.resource['access']['imageReference'],
            status=status,
            compressed_mb=info.compressed_layer_bytes / 1024 / 1024,
        ))
        logger.info(
            f'added {len(bom_resources)} BOM resource(s) for {info.resource["name"]!r}'
        )

    component['resources'].extend(new_resources)
    with open(component_descriptor_path, 'w') as f:
        yaml.safe_dump(cd_raw, f)

    logger.info(f'appended {len(new_resources)} BOM resource(s) to component descriptor')
    _write_step_summary(records)


if __name__ == '__main__':
    import argparse

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component-descriptor', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--cache-registry', required=True)
    parser.add_argument('--cache-repo-prefix', default='sbom-cache')
    parser.add_argument('--force-rescan', action='store_true', default=False,
                        help='ignore cache; re-scan all images and overwrite cached entries')
    args = parser.parse_args()

    process_external_resources(
        component_descriptor_path=args.component_descriptor,
        out_dir=args.out_dir,
        cache_registry=args.cache_registry,
        cache_repo_prefix=args.cache_repo_prefix,
        force_rescan=args.force_rescan,
    )
