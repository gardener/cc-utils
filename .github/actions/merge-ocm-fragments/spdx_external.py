#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Generate and cache SPDX and CycloneDX SBOM documents for external OCM OCI-image resources.

Processing is split into two phases:

  Phase 1 (parallel, unconstrained):
    For each external OCI image: resolve the platform-specific digest, fetch the manifest
    to obtain compressed layer sizes, and check the SBOM cache. Produces a list of cache
    hits (SBOM blobs already exist) and misses (syft scan required).

  Phase 2a (parallel, unconstrained):
    Download cached SBOM blobs for all cache hits.

  Phase 2b (resource-aware sequential/parallel):
    Run syft for cache misses. Before admitting each scan, check available memory and
    tmpfs space against the size estimate derived from the image manifest. At least one
    scan is always admitted to prevent deadlock. On completion, re-check resources and
    admit the next pending scan.

Cache addressing:
    <cache_registry>/<cache_repo_prefix>/<mangled-source-repo>:<cache-tag>

  <mangled-source-repo>  source image repository path with '/' and ':' replaced by '-'
  <cache-tag>            source image digest with ':' replaced by '-' (e.g. sha256-abc123…)

  Each cache manifest has two layers: layer[0] = SPDX JSON, layer[1] = CycloneDX JSON.

TMPDIR is forwarded to syft subprocesses and used as the parent for the per-scan
temporary directory, so both syft's layer unpacking and the output SBOM files land
on the same filesystem that is measured for available space.

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
import sbom.oci as osbom
import ocm

logger = logging.getLogger(__name__)


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
class _CacheHit:
    info: _ImageInfo


@dataclasses.dataclass
class _CacheMiss:
    info: _ImageInfo


class _ScanStatus(enum.Enum):
    RESOLVE_FAILED = 'resolve-failed'
    CACHE_HIT      = 'cache-hit'
    SCANNED        = 'scanned'
    SCAN_FAILED    = 'scan-failed'


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
) -> _CacheHit | _CacheMiss:
    source_ref = om.OciImageReference.to_image_ref(info.digest_ref)
    cref = _cache_ref(cache_registry, cache_prefix, source_ref, info.source_digest)
    res = oci_client.manifest_raw(cref, absent_ok=True)
    if res is not None:
        logger.info(f'{info.resource["name"]!r}: cache hit ({cref})')
        return _CacheHit(info=info)
    logger.info(f'{info.resource["name"]!r}: cache miss — syft scan required')
    return _CacheMiss(info=info)


def _download_cached_sboms(
    hit: _CacheHit,
    oci_client: oc.Client,
    cache_registry: str,
    cache_prefix: str,
) -> tuple[_ImageInfo, bytes, bytes] | None:
    source_ref = om.OciImageReference.to_image_ref(hit.info.digest_ref)
    cref = _cache_ref(cache_registry, cache_prefix, source_ref, hit.info.source_digest)
    cache_repo = _cache_repo(cache_registry, cache_prefix, source_ref)
    try:
        manifest_res = oci_client.manifest_raw(cref)
        manifest = manifest_res.json()
        spdx_digest = manifest['layers'][0]['digest']
        cdx_digest = manifest['layers'][1]['digest']
        spdx_bytes = oci_client.blob(image_reference=cache_repo, digest=spdx_digest).content
        cdx_bytes = oci_client.blob(image_reference=cache_repo, digest=cdx_digest).content
        return (hit.info, spdx_bytes, cdx_bytes)
    except Exception as e:
        logger.warning(f'{hit.info.resource["name"]!r}: failed to download cached SBOMs: {e}')
        return None


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
    cache_repo: str,
    source_digest: str,
    oci_client: oc.Client,
    tool_version: str | None,
) -> None:
    '''
    Push SPDX and CycloneDX blobs as a two-layer OCI manifest into the cache repo,
    addressed by a tag derived from source_digest (sha256:<hex> → sha256-<hex>).

    layer[0] = SPDX JSON, layer[1] = CycloneDX JSON.
    '''
    with open(spdx_path, 'rb') as f:
        spdx_bytes = f.read()
    with open(cdx_path, 'rb') as f:
        cdx_bytes = f.read()

    empty_config = b'{}'
    empty_config_digest = f'sha256:{hashlib.sha256(empty_config).hexdigest()}'
    oci_client.put_blob(
        image_reference=cache_repo,
        digest=empty_config_digest,
        octets_count=len(empty_config),
        data=empty_config,
        mimetype=osbom.OCI_EMPTY_CONFIG_MEDIA_TYPE,
    )

    layers = []
    for data, media_type in (
        (spdx_bytes, osbom.SPDX_JSON_MEDIA_TYPE),
        (cdx_bytes, osbom.CYCLONEDX_JSON_MEDIA_TYPE),
    ):
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
    miss: _CacheMiss,
    oci_client: oc.Client,
    cache_registry: str,
    cache_prefix: str,
    syft_ver: str | None,
    tmpdir: str,
) -> tuple[_ImageInfo, bytes, bytes] | None:
    info = miss.info
    resource_name = info.resource['name']
    source_ref = om.OciImageReference.to_image_ref(info.digest_ref)
    cache_repo = _cache_repo(cache_registry, cache_prefix, source_ref)

    try:
        with tempfile.TemporaryDirectory(dir=tmpdir) as tmp:
            spdx_path = os.path.join(tmp, 'sbom.spdx.json')
            cdx_path = os.path.join(tmp, 'sbom.cdx.json')
            logger.info(f'{resource_name!r}: running syft on {info.digest_ref}')
            _run_syft(info.digest_ref, spdx_path, cdx_path, tmpdir)
            logger.info(
                f'{resource_name!r}: pushing SBOMs to cache '
                f'{cache_repo}:{_cache_tag(info.source_digest)}'
            )
            _push_sboms_to_cache(
                spdx_path=spdx_path,
                cdx_path=cdx_path,
                cache_repo=cache_repo,
                source_digest=info.source_digest,
                oci_client=oci_client,
                tool_version=syft_ver,
            )
            with open(spdx_path, 'rb') as f:
                spdx_bytes = f.read()
            with open(cdx_path, 'rb') as f:
                cdx_bytes = f.read()
        return (info, spdx_bytes, cdx_bytes)
    except Exception as e:
        logger.warning(f'{resource_name!r}: scan failed: {e}')
        return None


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
    spdx_blob_digest: str,
    cdx_blob_digest: str,
    component_version: str,
    tool_ver: str | None,
) -> tuple[dict, dict]:
    '''Return (spdx_resource, cdx_resource) OCM resource dicts for one external image.'''
    resource = info.resource
    return osbom.build_sbom_ocm_resources(
        resource_name=resource['name'],
        version=resource.get('version') or component_version,
        source_image_ref=resource['access']['imageReference'],
        source_digest=info.source_digest,
        spdx_bytes=spdx_bytes,
        cdx_bytes=cdx_bytes,
        spdx_blob_digest=spdx_blob_digest,
        cdx_blob_digest=cdx_blob_digest,
        tool_ver=tool_ver,
    )


def _run_scans_resource_aware(
    misses: list[_CacheMiss],
    oci_client: oc.Client,
    cache_registry: str,
    cache_prefix: str,
    syft_ver: str | None,
    tmpdir: str,
) -> list[tuple[_ImageInfo, bytes, bytes]]:
    '''
    Admit syft scans one at a time, gated by available memory and tmpfs space.
    Always admits at least one job to prevent deadlock.
    '''
    results = []
    pending = list(misses)
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
            # admit as many as resources allow
            while pending:
                miss = pending[0]
                est_disk, est_mem = _estimate_bytes(miss.info)
                force = not running  # always admit if nothing is running
                if not _can_admit(est_disk, est_mem, force):
                    break
                pending.pop(0)
                reserved_disk += est_disk
                reserved_mem += est_mem
                f = executor.submit(
                    _scan_image,
                    miss,
                    oci_client,
                    cache_registry,
                    cache_prefix,
                    syft_ver,
                    tmpdir,
                )
                running[f] = (est_disk, est_mem)
                logger.info(
                    f'admitted scan for {miss.info.resource["name"]!r} '
                    f'(est disk={est_disk//1024//1024}MB '
                    f'mem={est_mem//1024//1024}MB, '
                    f'{len(running)} running, {len(pending)} pending)'
                )

            if not running:
                break  # nothing running and nothing pending

            done, _ = concurrent.futures.wait(
                running,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for f in done:
                est_disk, est_mem = running.pop(f)
                reserved_disk -= est_disk
                reserved_mem -= est_mem
                result = f.result()
                if result:
                    results.append(result)

    return results


def _write_step_summary(records: list[_ScanRecord]) -> None:
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_path:
        return

    hits    = [r for r in records if r.status is _ScanStatus.CACHE_HIT]
    scanned = [r for r in records if r.status is _ScanStatus.SCANNED]
    failed  = [r for r in records if r.status in (
        _ScanStatus.SCAN_FAILED, _ScanStatus.RESOLVE_FAILED,
    )]

    rows = '\n'.join(
        f'| `{r.resource_name}` | `{r.image_ref}` '
        f'| {r.compressed_mb:.1f} MB | {r.status.value} |'
        for r in records
    )

    summary = textwrap.dedent(f'''\
        ## SBOM (SPDX + CycloneDX) — external OCI images

        | | count |
        |---|---|
        | cache hits | {len(hits)} |
        | freshly scanned | {len(scanned)} |
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
    Scan external OCI image resources for SPDX and CycloneDX SBOM documents;
    patch results into the component descriptor.
    '''
    tmpdir = os.environ.get('RUNNER_TEMP') or os.environ.get('TMPDIR') or tempfile.gettempdir()
    logger.info(f'using tmpdir for syft: {tmpdir}')

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
    resolved = []
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
        logger.info('force-rescan: skipping cache check, treating all images as misses')
        hits = []
        misses = [_CacheMiss(info=i) for i in resolved]
    else:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            cache_results = list(executor.map(
                lambda i: _check_cache(i, oci_client, cache_registry, cache_repo_prefix),
                resolved,
            ))
        hits = [r for r in cache_results if isinstance(r, _CacheHit)]
        misses = [r for r in cache_results if isinstance(r, _CacheMiss)]
    logger.info(f'phase 1 done: {len(hits)} cache hit(s), {len(misses)} cache miss(es)')

    # --- phase 2a: parallel download of cached SBOM blobs ---
    scan_results: list[tuple[_ImageInfo, bytes, bytes, _ScanStatus]] = []

    if hits:
        logger.info(f'phase 2a: downloading {len(hits)} cached SBOM document(s)')
        with concurrent.futures.ThreadPoolExecutor() as executor:
            downloaded = list(executor.map(
                lambda h: _download_cached_sboms(h, oci_client, cache_registry, cache_repo_prefix),
                hits,
            ))
        for hit, result in zip(hits, downloaded):
            if result is not None:
                scan_results.append((result[0], result[1], result[2], _ScanStatus.CACHE_HIT))
            else:
                records.append(_ScanRecord(
                    resource_name=hit.info.resource['name'],
                    image_ref=hit.info.resource['access']['imageReference'],
                    status=_ScanStatus.SCAN_FAILED,
                    compressed_mb=hit.info.compressed_layer_bytes / 1024 / 1024,
                ))

    # --- phase 2b: resource-aware syft scans for cache misses ---
    syft_ver = _syft_version()
    if misses:
        logger.info(f'phase 2b: scanning {len(misses)} image(s) with syft {syft_ver or "?"}')
        for info, spdx_bytes, cdx_bytes in _run_scans_resource_aware(
            misses=misses,
            oci_client=oci_client,
            cache_registry=cache_registry,
            cache_prefix=cache_repo_prefix,
            syft_ver=syft_ver,
            tmpdir=tmpdir,
        ):
            scan_results.append((info, spdx_bytes, cdx_bytes, _ScanStatus.SCANNED))

        # record misses that produced no result as failures
        scanned_names = {info.resource['name'] for info, _, __, status in scan_results
                         if status is _ScanStatus.SCANNED}
        for miss in misses:
            if miss.info.resource['name'] not in scanned_names:
                records.append(_ScanRecord(
                    resource_name=miss.info.resource['name'],
                    image_ref=miss.info.resource['access']['imageReference'],
                    status=_ScanStatus.SCAN_FAILED,
                    compressed_mb=miss.info.compressed_layer_bytes / 1024 / 1024,
                ))

    if not scan_results:
        logger.info('no SBOM results to add to component descriptor')
        _write_step_summary(records)
        return

    blobs_dir = os.path.join(out_dir, 'blobs.d')
    os.makedirs(blobs_dir, exist_ok=True)

    new_resources = []
    for info, spdx_bytes, cdx_bytes, status in scan_results:
        spdx_digest = _store_blob(blobs_dir, spdx_bytes)
        cdx_digest = _store_blob(blobs_dir, cdx_bytes)
        tool_ver = _syft_version_from_spdx(spdx_bytes)
        spdx_resource, cdx_resource = _build_sbom_resources(
            info=info,
            spdx_bytes=spdx_bytes,
            cdx_bytes=cdx_bytes,
            spdx_blob_digest=spdx_digest,
            cdx_blob_digest=cdx_digest,
            component_version=component_version,
            tool_ver=tool_ver,
        )
        new_resources.extend((spdx_resource, cdx_resource))
        records.append(_ScanRecord(
            resource_name=info.resource['name'],
            image_ref=info.resource['access']['imageReference'],
            status=status,
            compressed_mb=info.compressed_layer_bytes / 1024 / 1024,
        ))
        logger.info(f'added SBOM resources for {info.resource["name"]!r}')

    component['resources'].extend(new_resources)
    with open(component_descriptor_path, 'w') as f:
        yaml.safe_dump(cd_raw, f)

    logger.info(f'appended {len(new_resources)} SBOM resource(s) to component descriptor')
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
