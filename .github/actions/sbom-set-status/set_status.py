#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Set the Cumulus release status for a given GCS run-key.

Smart transition logic:
  - release-candidate, promoted: single write.
  - deployed-to-production, released: require promoted to have been written first
    (it registers the run-key with Cumulus). If the current status is not already
    promoted/deployed-to-production/released, a promoted write is prepended automatically.
  - released only: additionally polls until sbom/ is locked (confirms Cumulus has
    processed the promoted write) before writing released.
'''
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request


_GCS_BASE = 'https://storage.googleapis.com/storage/v1/b'
_GCS_UPLOAD = 'https://storage.googleapis.com/upload/storage/v1/b'

# statuses that count as "promoted already done" for ordering purposes
_PROMOTED_DONE = {'promoted', 'deployed-to-production', 'released'}
# statuses that require a prior promoted write
_NEEDS_PROMOTED = {'deployed-to-production', 'released'}

VALID_STATUSES = {
    'release-candidate',
    'promoted',
    'deployed-to-production',
    'released',
}


def _current_status(bucket: str, token: str, run_key: str) -> str | None:
    '''Return the most recent releaseStatus for run_key, or None if absent.'''
    prefix = f'{run_key}/.status-log/release/'
    url = (
        f'{_GCS_BASE}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?prefix={urllib.parse.quote(prefix, safe="")}'
    )
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req) as r:  # nosec B310
            body = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    items = body.get('items', [])
    if not items:
        return None
    latest = sorted(items, key=lambda i: i['name'])[-1]
    req2 = urllib.request.Request(
        latest['mediaLink'],
        headers={'Authorization': f'Bearer {token}'},
    )
    with urllib.request.urlopen(req2) as r:  # nosec B310
        return json.load(r).get('releaseStatus')


def _write_status(bucket: str, token: str, run_key: str, status: str) -> None:
    ts = int(time.time())
    blob = f'{run_key}/.status-log/release/release-status-{ts}.json'
    url = (
        f'{_GCS_UPLOAD}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?uploadType=media&name={urllib.parse.quote(blob, safe="")}'
    )
    data = json.dumps({'releaseStatus': status}).encode()
    req = urllib.request.Request(url, data=data, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req) as r:  # nosec B310
        r.read()
    print(f'wrote status: {status}', file=sys.stderr)


def _poll_sbom_locked(
    bucket: str,
    token: str,
    run_key: str,
    interval: int,
    timeout: int,
) -> None:
    '''Poll a canary write to sbom/ until Cumulus applies the promoted lock (HTTP 403).'''
    canary = f'{run_key}/.sbom-set-status-probe'
    url = (
        f'{_GCS_UPLOAD}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?uploadType=media&name={urllib.parse.quote(canary, safe="")}'
    )
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(
            url,
            data=b'probe',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'text/plain',
            },
        )
        try:
            with urllib.request.urlopen(req) as r:  # nosec B310
                r.read()
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        print(f'poll sbom/ lock: attempt {attempt}, HTTP {code}', file=sys.stderr)
        if code == 403:
            print('sbom/ locked — promoted registered with Cumulus', file=sys.stderr)
            return
        if time.monotonic() >= deadline:
            print(
                f'warning: sbom/ not locked after {timeout}s — proceeding anyway',
                file=sys.stderr,
            )
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gcs-bucket', required=True, metavar='BUCKET')
    parser.add_argument('--gcs-token', required=True, metavar='TOKEN')
    parser.add_argument('--run-key', required=True, metavar='KEY')
    parser.add_argument(
        '--release-status',
        required=True,
        choices=sorted(VALID_STATUSES),
        metavar='STATUS',
        help=f'target release status; one of: {", ".join(sorted(VALID_STATUSES))}',
    )
    parser.add_argument(
        '--on-already-released',
        choices=('skip', 'fail'),
        default='skip',
        metavar='ACTION',
        help='"skip" exits 0, "fail" exits 1 when run-key is already released (default: skip)',
    )
    parser.add_argument(
        '--poll-interval',
        type=int,
        default=30,
        metavar='SECONDS',
        help='seconds between lock-poll attempts when transitioning to released (default: 30)',
    )
    parser.add_argument(
        '--poll-timeout',
        type=int,
        default=600,
        metavar='SECONDS',
        help='max seconds to wait for sbom/ lock before proceeding (default: 600)',
    )
    args = parser.parse_args()

    bucket = args.gcs_bucket
    token = args.gcs_token
    run_key = args.run_key
    target = args.release_status

    current = _current_status(bucket, token, run_key)
    print(f'run-key: {run_key!r}  current: {current!r}  target: {target!r}', file=sys.stderr)

    if current == 'released':
        msg = f'run-key {run_key!r} is already in "released" state'
        if args.on_already_released == 'fail':
            print(f'error: {msg}', file=sys.stderr)
            sys.exit(1)
        print(f'{msg} — skipping', file=sys.stderr)
        return

    # pre-write promoted if the target requires it and it hasn't been done yet
    if target in _NEEDS_PROMOTED and current not in _PROMOTED_DONE:
        print('pre-writing promoted (required before {})'.format(target), file=sys.stderr)
        _write_status(bucket, token, run_key, 'promoted')

    # for released: wait until Cumulus has processed the promoted write (sbom/ locked)
    if target == 'released':
        _poll_sbom_locked(bucket, token, run_key, args.poll_interval, args.poll_timeout)

    _write_status(bucket, token, run_key, target)


if __name__ == '__main__':
    main()
