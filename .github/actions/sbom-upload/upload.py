#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import argparse
import concurrent.futures
import datetime
import email.utils
import json
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
        return (mapping.sbom.extraIdentity or {}).get('sbom-format')
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
        if k not in ('sbom-format', 'version')
    ]
    extra = ('-' + '-'.join(extra_vals)) if extra_vals else ''
    return (
        f'{_mangle(component.name)}:{component.version}'
        f'_{_mangle(resource.name)}:{resource.version}'
        f'{extra}.sbom.{fmt_id}'
    )


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

    def acquire(self) -> None:
        with self._cv:
            self._cv.wait_for(lambda: self._active < self._target)
            self._active += 1

    def release(self, grew: bool = False, shrank: bool = False) -> None:
        with self._cv:
            self._active -= 1
            if grew and self._target < self._hi:
                self._target += 1
            elif shrank:
                self._target = max(self._lo, self._target // 2)
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
    gcs_token: str,
    data: bytes,
    blob: str,
) -> str:
    # 429 → AIMD backoff, retry up to 20×; 5xx → retry 3×; other 4xx → fail fast
    MAX_429 = 20
    MAX_5XX = 3
    url = (
        'https://storage.googleapis.com/upload/storage/v1/b/'
        f'{urllib.parse.quote(bucket, safe="")}/o'
        f'?uploadType=media&name={urllib.parse.quote(blob, safe="")}'
    )
    n429 = 0
    n5xx = 0
    while True:
        throttle.acquire()
        try:
            req = urllib.request.Request(url, data=data, headers={
                'Authorization': f'Bearer {gcs_token}',
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
            elif e.code >= 500:
                n5xx += 1
                throttle.release()
                if n5xx >= MAX_5XX:
                    raise
                time.sleep(2 ** n5xx)
            else:
                throttle.release()
                raise  # 4xx non-429: not transient
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
    gcs_token = args.gcs_token
    gcs_prefix = f'sbom/{version}'

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
    existing_blobs = _gcs_list_prefix(bucket, gcs_token, gcs_prefix + '/')

    # collect work items; skip resource entirely if any blobs exist for it already
    # (avoids OCI referrer calls — dominant cost on re-runs)
    work = []
    skipped = 0
    for node in ocm_iter.iter_resources(component=root_component, lookup=lookup):
        resource_prefix = (
            f'{gcs_prefix}/'
            f'{_mangle(node.component.name)}:{node.component.version}'
            f'_{_mangle(node.resource.name)}:{node.resource.version}'
        )
        already = [b for b in existing_blobs if b.startswith(resource_prefix)]
        if already:
            skipped += len(already)
            continue
        for mapping in si.iter_sboms_for_resource(
            resource=node.resource,
            component=node.component,
            oci_client=oci_client,
        ):
            fmt_id = _fmt_id(mapping)
            if fmt_id:
                blob = f'{gcs_prefix}/{_filename(node.component, node.resource, fmt_id)}'
                if blob not in existing_blobs:
                    work.append((node, mapping, fmt_id, blob))
                else:
                    skipped += 1

    uploaded = 0
    errors = 0

    def _process(item: tuple) -> str:
        node, mapping, fmt_id, blob = item
        try:
            data = si.fetch_sbom_document(mapping, oci_client)
        except Exception as e:
            print(f'error fetching {node.resource.name} ({fmt_id}): {e}', file=sys.stderr)
            return 'error'
        try:
            blob_name = _gcs_upload(throttle, bucket, gcs_token, data, blob)
        except Exception as e:
            print(f'error uploading {node.resource.name} ({fmt_id}): {e}', file=sys.stderr)
            return 'error'
        print(f'gs://{bucket}/{blob_name}')
        return 'ok'

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for result in pool.map(_process, work):
            if result == 'ok':
                uploaded += 1
            else:
                errors += 1

    parts = [f'{uploaded} uploaded']
    if skipped:
        parts.append(f'{skipped} skipped (already present)')
    print(', '.join(parts) + '.', file=sys.stderr)
    if errors:
        print(f'{errors} error(s).', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
