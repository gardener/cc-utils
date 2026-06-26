#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import argparse
import concurrent.futures
import dataclasses
import datetime
import email.utils
import json
import os
import random
import sys
import threading
import time
import urllib.parse
import urllib.request

import cnudie.retrieve
import oci.auth
import oci.client
import ocm
import ocm.iter as ocm_iter
import sbom.iter as si
import sbom.oci as soci


def _mangle(s: str) -> str:
    return s.replace('/', '_')


def _fmt_id(mapping: si.SbomMapping) -> str | None:
    if mapping.source is si.SbomSource.OCM:
        ei = mapping.sbom.extraIdentity or {}
        return ei.get('sbom-format') or ei.get('cbom-format') or None
    for fid, media_type in soci.SBOM_FORMATS:
        if mapping.sbom.artifact_type == media_type:
            return fid
    return None


def _filename(
    component: ocm.Component,
    resource: ocm.Resource,
    fmt_id: str,
) -> str:
    extra_vals = [
        v for k, v in sorted((resource.extraIdentity or {}).items())
        if k not in ('sbom-format', 'cbom-format', 'version')
    ]
    extra = ('-' + '-'.join(extra_vals)) if extra_vals else ''
    return (
        f'{_mangle(component.name)}:{component.version}'
        f'_{_mangle(resource.name)}:{resource.version}'
        f'{extra}.sbom.{fmt_id}'
    )


def _fmt_bytes(n: int) -> str:
    v = float(n)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if v < 1024 or unit == 'GiB':
            return f'{v:.0f} {unit}' if unit == 'B' else f'{v:.1f} {unit}'
        v /= 1024


@dataclasses.dataclass
class _TokenRefreshConfig:
    api_url: str
    oidc_audience: str
    pipeline_id: str
    pipeline_group_id: str
    scope: str


def _fetch_token(cfg: _TokenRefreshConfig) -> str:
    '''Perform dual-token exchange against System Trust; return scoped GCS token.'''
    # Step 1: GitHub OIDC token
    actions_token_request_token = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN', '')
    actions_token_request_url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL', '')
    if not actions_token_request_token or not actions_token_request_url:
        raise RuntimeError('ACTIONS_ID_TOKEN_REQUEST_TOKEN / _URL not set; cannot refresh token')

    oidc_url = f'{actions_token_request_url}&audience={urllib.parse.quote(cfg.oidc_audience)}'
    req = urllib.request.Request(
        oidc_url,
        headers={'Authorization': f'Bearer {actions_token_request_token}'},
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        actions_token = json.load(r)['value']

    # Step 2: GCP instance-identity token
    gcp_meta_url = (
        'http://metadata/computeMetadata/v1/instance/service-accounts/default/identity'
        f'?audience={urllib.parse.quote(cfg.oidc_audience)}&format=full'
    )
    req = urllib.request.Request(
        gcp_meta_url,
        headers={'Metadata-Flavor': 'Google'},
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        gcp_token = r.read().decode()

    # Step 3: System Trust /auth
    group_enc = urllib.parse.quote(cfg.pipeline_group_id, safe='')
    pipeline_enc = urllib.parse.quote(cfg.pipeline_id, safe='')
    req = urllib.request.Request(
        f'{cfg.api_url}/auth?group_id={group_enc}&pipeline_id={pipeline_enc}',
        headers={
            'X-Trust-Authorization-Orchestrator': actions_token,
            'X-Trust-Authorization-Runtime': gcp_token,
        },
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        session_token = json.load(r)['token']

    # Step 4: System Trust /tokens
    scope_enc = urllib.parse.quote(cfg.scope, safe='')
    req = urllib.request.Request(
        f'{cfg.api_url}/tokens?systems={scope_enc}',
        headers={'Authorization': f'Bearer {session_token}'},
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        tokens = json.load(r)
    return tokens[cfg.scope]


def _make_token_lookup(
    initial_token: str,
    refresh_cfg: '_TokenRefreshConfig | None',
):
    '''
    Return a thread-safe token_lookup(refresh=False) closure.

    token_lookup()              → current cached token
    token_lookup(refresh=True)  → re-fetch via System Trust, update cache, return new token
    '''
    lock = threading.Lock()
    state = [initial_token]

    def token_lookup(refresh: bool = False) -> str:
        if not refresh:
            with lock:
                return state[0]
        if refresh_cfg is None:
            raise RuntimeError('no refresh config — cannot re-fetch token')
        t0 = time.monotonic()
        print('GCS token expired — re-fetching via System Trust ...', file=sys.stderr)
        new_token = _fetch_token(refresh_cfg)
        with lock:
            state[0] = new_token
        print(f'GCS token refreshed in {time.monotonic() - t0:.1f}s', file=sys.stderr)
        return new_token

    return token_lookup


class _UploadThrottle:
    '''
    Adaptive upload concurrency (AIMD):
      success → target += 1 (additive increase, up to hi)
      429     → target = max(lo, target // 2) (multiplicative decrease)
    '''
    def __init__(self, initial: int = 4, lo: int = 1, hi: int = 8):
        self._cv = threading.Condition(threading.Lock())
        self._target = initial
        self._active = 0
        self._lo = lo
        self._hi = hi
        print(f'concurrency: {initial} workers (AIMD, lo={lo} hi={hi})', file=sys.stderr)

    def acquire(self) -> None:
        with self._cv:
            self._cv.wait_for(lambda: self._active < self._target)
            self._active += 1

    def release(self, grew: bool = False, shrank: bool = False) -> None:
        with self._cv:
            self._active -= 1
            old = self._target
            if grew and self._target < self._hi:
                self._target += 1
            elif shrank:
                self._target = max(self._lo, self._target // 2)
            if self._target != old:
                print(f'concurrency: {old} → {self._target}', file=sys.stderr)
            self._cv.notify_all()


def _retry_after(headers, attempt: int) -> float:
    '''Parse Retry-After header (integer seconds or HTTP-date); fall back to exponential backoff.'''
    raw = headers.get('Retry-After', '')
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            dt = email.utils.parsedate_to_datetime(raw)
            wait = (dt - datetime.datetime.now(tz=datetime.timezone.utc)).total_seconds()
            return max(0, wait)
        except Exception:  # nosec B110
            pass
    return min(2 ** attempt, 120)


def _gcs_list_prefix(bucket: str, gcs_token: str, prefix: str) -> set[str]:
    '''Return set of object names already present under prefix.'''
    existing = set()
    page_token = None
    while True:
        params = f'prefix={urllib.parse.quote(prefix, safe="")}'
        if page_token:
            params += f'&pageToken={urllib.parse.quote(page_token, safe="")}'
        url = (
            'https://storage.googleapis.com/storage/v1/b/'
            f'{urllib.parse.quote(bucket, safe="")}/o?{params}'
        )
        req = urllib.request.Request(url, headers={
            'Authorization': f'Bearer {gcs_token}',
        })
        with urllib.request.urlopen(req) as r:  # nosec B310
            body = json.load(r)
        for item in body.get('items', []):
            existing.add(item['name'])
        page_token = body.get('nextPageToken')
        if not page_token:
            break
    return existing


def _gcs_upload(
    throttle: _UploadThrottle,
    bucket: str,
    token_lookup,
    data: bytes,
    blob: str,
) -> str:
    # 429 → AIMD backoff, retry up to 20×; 5xx → retry 3×; 401 → token refresh + 1 retry;
    # other 4xx → fail fast
    MAX_429 = 20
    MAX_5XX = 3
    url = (
        'https://storage.googleapis.com/upload/storage/v1/b/'
        f'{urllib.parse.quote(bucket, safe="")}/o'
        f'?uploadType=media&name={urllib.parse.quote(blob, safe="")}'
    )
    n429 = 0
    n5xx = 0
    refreshed = False
    while True:
        throttle.acquire()
        try:
            req = urllib.request.Request(url, data=data, headers={
                'Authorization': f'Bearer {token_lookup()}',
                'Content-Type': 'application/octet-stream',
            })
            with urllib.request.urlopen(req) as r:  # nosec B310
                blob_name = json.load(r)['name']
            throttle.release(grew=True)
            return blob_name
        except urllib.error.HTTPError as e:
            if e.code == 429:
                n429 += 1
                throttle.release(shrank=True)
                if n429 >= MAX_429:
                    raise
                wait = _retry_after(e.headers, n429)
                time.sleep(wait + random.uniform(0, 2))
            elif e.code == 401 and not refreshed:
                # token likely expired — refresh once and retry
                throttle.release()
                token_lookup(refresh=True)
                refreshed = True
            elif e.code >= 500:
                n5xx += 1
                throttle.release()
                if n5xx >= MAX_5XX:
                    raise
                time.sleep(2 ** n5xx)
            else:
                throttle.release()
                raise  # 4xx non-429/non-401: not transient
        except Exception:
            throttle.release()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Fetch SBOMs for all resources in an OCM component tree and upload to GCS.',
    )
    parser.add_argument(
        '--ocm-component',
        required=True,
        metavar='NAME:VERSION',
        help='root component in name:version format',
    )
    parser.add_argument(
        '--ocm-repository',
        required=True,
        action='append',
        dest='ocm_repositories',
        metavar='URL',
        help='OCM repository base URL; may be repeated',
    )
    parser.add_argument('--gcs-bucket', required=True, metavar='BUCKET')
    parser.add_argument('--gcs-token', required=True, metavar='TOKEN')
    parser.add_argument('--run-key', default='', metavar='KEY',
        help='GCS path prefix (run key); defaults to OCM component version')
    # token refresh (optional — enables mid-run re-auth on 401)
    parser.add_argument('--token-exchange-api-url', default='', metavar='URL')
    parser.add_argument('--token-exchange-audience', default='', metavar='AUD')
    parser.add_argument('--token-exchange-pipeline-id', default='', metavar='ID')
    parser.add_argument('--token-exchange-pipeline-group-id', default='', metavar='GID')
    parser.add_argument('--token-exchange-scope', default='', metavar='SCOPE')
    args = parser.parse_args()

    errors = []
    if not args.gcs_token.strip():
        errors.append('--gcs-token is empty (token exchange may have failed)')
    if not args.gcs_bucket.strip():
        errors.append('--gcs-bucket is empty')
    ocm_repositories = [r for r in args.ocm_repositories if r.strip()]
    if not ocm_repositories:
        errors.append('--ocm-repository: no non-empty values')
    if ':' not in args.ocm_component:
        errors.append(f'--ocm-component must be name:version, got: {args.ocm_component!r}')
    if errors:
        for msg in errors:
            print(f'error: {msg}', file=sys.stderr)
        sys.exit(1)

    name, version = args.ocm_component.rsplit(':', 1)
    bucket = args.gcs_bucket
    gcs_prefix = f'{args.run_key or version}/sbom'

    refresh_cfg_args = (
        args.token_exchange_api_url,
        args.token_exchange_audience,
        args.token_exchange_pipeline_id,
        args.token_exchange_pipeline_group_id,
        args.token_exchange_scope,
    )
    if all(refresh_cfg_args):
        refresh_cfg = _TokenRefreshConfig(*refresh_cfg_args)
        print('token auto-refresh: enabled', file=sys.stderr)
    else:
        refresh_cfg = None
        print('token auto-refresh: disabled (no refresh args supplied)', file=sys.stderr)

    token_lookup = _make_token_lookup(
        initial_token=args.gcs_token,
        refresh_cfg=refresh_cfg,
    )

    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(absent_ok=True),
    )

    ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(*ocm_repositories)
    lookup = cnudie.retrieve.composite_component_descriptor_lookup(
        lookups=(
            cnudie.retrieve.in_memory_cache_component_descriptor_lookup(
                ocm_repository_lookup=ocm_repo_lookup,
            ),
            cnudie.retrieve.oci_component_descriptor_lookup(
                ocm_repository_lookup=ocm_repo_lookup,
                oci_client=oci_client,
            ),
        ),
        ocm_repository_lookup=ocm_repo_lookup,
    )

    root_component = lookup(
        ocm.ComponentIdentity(name=name, version=version),
    ).component

    throttle = _UploadThrottle()
    existing_blobs = _gcs_list_prefix(bucket, token_lookup(), gcs_prefix + '/')

    # phase 1: discover SBOMs — OCI referrer calls are the dominant cost on first runs;
    # parallelise across resources, dedup serially from results.
    all_nodes = list(ocm_iter.iter_resources(component=root_component, lookup=lookup))
    print(f'discovered {len(all_nodes)} resources', file=sys.stderr)

    def _discover(node) -> tuple:
        '''Return (skipped_count, [(node, mapping, fmt_id, blob), ...]).'''
        resource_prefix = (
            f'{gcs_prefix}/'
            f'{_mangle(node.component.name)}:{node.component.version}'
            f'_{_mangle(node.resource.name)}:{node.resource.version}'
        )
        already = [b for b in existing_blobs if b.startswith(resource_prefix)]
        if already:
            return len(already), []
        items = []
        for mapping in si.iter_sboms_for_resource(
            resource=node.resource,
            component=node.component,
            oci_client=oci_client,
        ):
            fmt_id = _fmt_id(mapping)
            if fmt_id:
                blob = f'{gcs_prefix}/{_filename(node.component, node.resource, fmt_id)}'
                items.append((node, mapping, fmt_id, blob))
        return 0, items

    work = []
    skipped = 0
    duplicates = 0
    planned_blobs = set(existing_blobs)  # dedup within run (OCM + OCI referrer may overlap)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for n_skipped, items in pool.map(_discover, all_nodes):
            skipped += n_skipped
            for item in items:
                _, _, _, blob = item
                if blob not in planned_blobs:
                    work.append(item)
                    planned_blobs.add(blob)
                else:
                    duplicates += 1

    print(
        f'{len(work)} to upload, {skipped} already present, {duplicates} duplicates suppressed',
        file=sys.stderr,
    )

    uploaded = 0
    errors = 0
    total_bytes = 0
    total_fetch_s = 0.0
    total_upload_s = 0.0
    stats_lock = threading.Lock()

    def _process(item: tuple) -> bool:
        node, mapping, fmt_id, blob = item
        t0 = time.monotonic()
        try:
            data = si.fetch_sbom_document(mapping, oci_client)
        except Exception as e:
            print(f'error fetching {node.resource.name} ({fmt_id}): {e}', file=sys.stderr)
            return False
        fetch_s = time.monotonic() - t0
        nbytes = len(data)
        t1 = time.monotonic()
        try:
            blob_name = _gcs_upload(throttle, bucket, token_lookup, data, blob)
        except Exception as e:
            print(f'error uploading {node.resource.name} ({fmt_id}): {e}', file=sys.stderr)
            return False
        upload_s = time.monotonic() - t1
        print(f'gs://{bucket}/{blob_name}')
        with stats_lock:
            nonlocal total_bytes, total_fetch_s, total_upload_s
            total_bytes += nbytes
            total_fetch_s += fetch_s
            total_upload_s += upload_s
        return True

    t_start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for ok in pool.map(_process, work):
            if ok:
                uploaded += 1
            else:
                errors += 1
    elapsed = time.monotonic() - t_start

    parts = [f'{uploaded} uploaded ({_fmt_bytes(total_bytes)})']
    if skipped:
        parts.append(f'{skipped} skipped (already present)')
    if duplicates:
        parts.append(f'{duplicates} duplicate sources suppressed')
    if errors:
        parts.append(f'{errors} error(s)')
    print(', '.join(parts) + f' — {elapsed:.0f}s elapsed', file=sys.stderr)
    if uploaded:
        print(
            f'fetch: {total_fetch_s:.1f}s cumulative'
            f'  upload: {total_upload_s:.1f}s cumulative'
            f'  throughput: {_fmt_bytes(int(total_bytes / elapsed))}/s',
            file=sys.stderr,
        )
    if errors:
        sys.exit(1)


if __name__ == '__main__':
    main()
