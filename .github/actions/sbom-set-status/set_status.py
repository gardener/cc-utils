#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Set the Cumulus release status for one or more GCS run-keys.

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
import os
import sys
import time
import urllib.parse
import urllib.request


_GCS_BASE = 'https://storage.googleapis.com/storage/v1/b'
_GCS_UPLOAD = 'https://storage.googleapis.com/upload/storage/v1/b'
_MAX_RESPONSE = 65536  # cap on raw response reads (tokens, write-ACKs)


def _read_capped(r, label: str = 'response') -> bytes:
    data = r.read(_MAX_RESPONSE + 1)
    if len(data) > _MAX_RESPONSE:
        raise RuntimeError(f'{label} exceeds {_MAX_RESPONSE} bytes — aborting')
    return data


def _fetch_token(
    api_url: str,
    oidc_audience: str,
    pipeline_id: str,
    pipeline_group_id: str,
    scope: str,
) -> str:
    '''Dual-token exchange against System Trust; return scoped GCS token.'''
    request_token = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN', '')
    request_url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL', '')
    if not request_token or not request_url:
        raise RuntimeError('ACTIONS_ID_TOKEN_REQUEST_TOKEN/_URL not set; cannot refresh token')

    req = urllib.request.Request(
        f'{request_url}&audience={urllib.parse.quote(oidc_audience)}',
        headers={'Authorization': f'Bearer {request_token}'},
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        actions_token = json.load(r)['value']

    req = urllib.request.Request(
        'http://metadata/computeMetadata/v1/instance/service-accounts/default/identity'
        f'?audience={urllib.parse.quote(oidc_audience)}&format=full',
        headers={'Metadata-Flavor': 'Google'},
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        gcp_token = _read_capped(r, 'GCP identity token').decode()

    req = urllib.request.Request(
        f'{api_url}/auth'
        f'?group_id={urllib.parse.quote(pipeline_group_id, safe="")}'
        f'&pipeline_id={urllib.parse.quote(pipeline_id, safe="")}',
        headers={
            'X-Trust-Authorization-Orchestrator': actions_token,
            'X-Trust-Authorization-Runtime': gcp_token,
        },
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        session_token = json.load(r)['token']

    req = urllib.request.Request(
        f'{api_url}/tokens?systems={urllib.parse.quote(scope, safe="")}',
        headers={'Authorization': f'Bearer {session_token}'},
    )
    with urllib.request.urlopen(req) as r:  # nosec B310
        return json.load(r)[scope]


# NOTE: _fetch_token is duplicated verbatim in sbom-upload/upload.py — keep in sync.

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


def _current_status(bucket: str, token: str, run_key: str, refresh=None) -> str | None:
    '''Return the most recent releaseStatus for run_key, or None if absent.'''
    prefix = f'{run_key}/.status-log/release/'
    url = (
        f'{_GCS_BASE}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?prefix={urllib.parse.quote(prefix, safe="")}'
    )
    refreshed = False
    while True:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(req) as r:  # nosec B310
                body = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 401 and not refreshed and refresh is not None:
                print('GCS token expired — refreshing ...', file=sys.stderr)
                token = refresh()
                refreshed = True
                continue
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


def _write_status(bucket: str, token: str, run_key: str, status: str, refresh=None) -> str:
    '''Write status entry; refreshes token on 401; returns the token (possibly refreshed).'''
    ts = int(time.time())
    blob = f'{run_key}/.status-log/release/release-status-{ts}.json'
    url = (
        f'{_GCS_UPLOAD}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?uploadType=media&name={urllib.parse.quote(blob, safe="")}'
    )
    data = json.dumps({'releaseStatus': status}).encode()
    refreshed = False
    while True:
        req = urllib.request.Request(url, data=data, headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        })
        try:
            with urllib.request.urlopen(req) as r:  # nosec B310
                _read_capped(r, 'GCS write response')
            print(f'wrote status: {status}', file=sys.stderr)
            return token
        except urllib.error.HTTPError as e:
            if e.code == 401 and not refreshed and refresh is not None:
                print('GCS token expired — refreshing ...', file=sys.stderr)
                token = refresh()
                refreshed = True
                continue
            raise


def _first_sbom_object(bucket: str, token: str, run_key: str) -> str | None:
    '''Return the name of the first object under <run_key>/sbom/, or None.'''
    prefix = f'{run_key}/sbom/'
    url = (
        f'{_GCS_BASE}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?prefix={urllib.parse.quote(prefix, safe="")}&maxResults=1'
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
    return items[0]['name'] if items else None


def _poll_sbom_locked(
    bucket: str,
    token: str,
    run_key: str,
    interval: int,
    timeout: int,
    refresh=None,
) -> str:
    '''Poll an overwrite of an existing sbom/ object until Cumulus applies the hold (HTTP 403).

    Cumulus locks by setting per-object holds on existing objects, not path-level IAM.
    A new-object write always succeeds (200); only overwriting a held object returns 403.
    If no sbom/ objects exist, the lock can never fire — skip immediately.
    Returns the (possibly refreshed) token.
    '''
    probe = _first_sbom_object(bucket, token, run_key)
    if probe is None:
        print('no sbom/ objects found — skipping lock poll', file=sys.stderr)
        return token
    url = (
        f'{_GCS_UPLOAD}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?uploadType=media&name={urllib.parse.quote(probe, safe="")}'
    )
    deadline = time.monotonic() + timeout
    attempt = 0
    refreshed = False
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
                _read_capped(r, 'GCS lock-probe response')
            code = 200
        except urllib.error.HTTPError as e:
            if e.code == 401 and not refreshed and refresh is not None:
                print('GCS token expired — refreshing ...', file=sys.stderr)
                token = refresh()
                refreshed = True
                continue
            code = e.code
        if code == 403:
            print(f'immutability confirmed for {probe} (attempt {attempt})', file=sys.stderr)
            return token
        print(f'waiting for Cumulus hold on {probe}: attempt {attempt} ({code})', file=sys.stderr)
        if time.monotonic() >= deadline:
            print(
                f'warning: sbom/ not locked after {timeout}s — proceeding anyway',
                file=sys.stderr,
            )
            return token
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gcs-bucket', required=True, metavar='BUCKET')
    parser.add_argument('--gcs-token', required=True, metavar='TOKEN')

    key_group = parser.add_mutually_exclusive_group(required=True)
    key_group.add_argument('--run-key', metavar='KEY',
        help='single GCS run-key to set status on')
    key_group.add_argument('--run-keys', metavar='KEYS',
        help='newline-separated list of GCS run-keys to set status on')

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
        '--on-absent',
        choices=('skip', 'fail'),
        default='skip',
        metavar='ACTION',
        help='"skip" ignores run-keys with no status entries, "fail" aborts (default: skip)',
    )
    parser.add_argument(
        '--mode',
        choices=('create', 'update', 'create-or-update'),
        default='create-or-update',
        metavar='MODE',
        help=(
            '"create": fail if status entries already exist; '
            '"update": skip/fail if absent (on-absent applies); '
            '"create-or-update": write unconditionally (default: create-or-update)'
        ),
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
    # token refresh — optional; enables re-auth on 401 mid-run
    parser.add_argument('--token-exchange-api-url', default='', metavar='URL')
    parser.add_argument('--token-exchange-audience', default='', metavar='AUD')
    parser.add_argument('--token-exchange-pipeline-id', default='', metavar='ID')
    parser.add_argument('--token-exchange-pipeline-group-id', default='', metavar='GID')
    parser.add_argument('--token-exchange-scope', default='', metavar='SCOPE')
    args = parser.parse_args()

    bucket = args.gcs_bucket
    token = args.gcs_token
    target = args.release_status

    refresh_args = (
        args.token_exchange_api_url,
        args.token_exchange_audience,
        args.token_exchange_pipeline_id,
        args.token_exchange_pipeline_group_id,
        args.token_exchange_scope,
    )
    if all(refresh_args):
        def refresh():
            nonlocal token
            token = _fetch_token(*refresh_args)
            return token
        print('token auto-refresh: enabled', file=sys.stderr)
    else:
        refresh = None
        print('token auto-refresh: disabled', file=sys.stderr)

    if args.run_keys:
        run_keys = [k.strip() for k in args.run_keys.splitlines() if k.strip()]
    else:
        run_keys = [args.run_key]

    for run_key in run_keys:
        current = _current_status(bucket, token, run_key, refresh=refresh)
        current_s = current if current is not None else '(none)'
        print(
            f'setting status for run-key={run_key!r} to {target!r} (was: {current_s})',
            file=sys.stderr,
        )

        if current is None:
            if args.mode == 'update':
                if args.on_absent == 'fail':
                    print(f'error: run-key {run_key!r} has no status entries', file=sys.stderr)
                    sys.exit(1)
                print(f'run-key {run_key!r} absent — skipping', file=sys.stderr)
                continue
            # create / create-or-update: absence is fine — fall through to write
        elif args.mode == 'create':
            print(
                f'error: run-key {run_key!r} already has status {current!r}',
                file=sys.stderr,
            )
            sys.exit(1)

        if current == 'released':
            msg = f'run-key {run_key!r} is already in "released" state'
            if args.on_already_released == 'fail':
                print(f'error: {msg}', file=sys.stderr)
                sys.exit(1)
            print(f'{msg} — skipping', file=sys.stderr)
            continue

        # pre-write promoted if the target requires it and it hasn't been done yet
        if target in _NEEDS_PROMOTED and current not in _PROMOTED_DONE:
            print('pre-writing promoted (required before {})'.format(target), file=sys.stderr)
            token = _write_status(bucket, token, run_key, 'promoted', refresh=refresh)

        # for released: wait until Cumulus has processed the promoted write (sbom/ locked)
        if target == 'released':
            token = _poll_sbom_locked(
                bucket, token, run_key, args.poll_interval, args.poll_timeout, refresh=refresh,
            )

        _write_status(bucket, token, run_key, target, refresh=refresh)


if __name__ == '__main__':
    main()
