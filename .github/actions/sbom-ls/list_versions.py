#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
List versions present in a Cumulus GCS bucket, ordered greatest-to-smallest (semver),
with the current release status of each.

Version normalisation: bare integers or two-component strings are padded to three components
(e.g. "1" → "1.0.0", "1.2" → "1.2.0").  Non-numeric / non-semver run-keys are listed last.

Output (stdout): JSON array of objects:
  [{"version": "1.2.3", "status": "promoted"}, ...]
  status is null when no status file has been written yet.
'''
import argparse
import json
import sys
import urllib.parse
import urllib.request


_GCS_BASE = 'https://storage.googleapis.com/storage/v1/b'


def _normalise(v: str) -> tuple[int, ...] | None:
    '''Return (major, minor, patch) tuple, or None if v is not semver-like.'''
    parts = v.split('.')
    if not (1 <= len(parts) <= 3):
        return None
    try:
        ints = [int(p) for p in parts]
    except ValueError:
        return None
    while len(ints) < 3:
        ints.append(0)
    return tuple(ints)


def _list_prefixes(bucket: str, token: str) -> list[str]:
    '''Return all top-level "directories" (virtual prefixes) in the bucket.'''
    prefixes = []
    page_token = None
    while True:
        params = 'delimiter=%2F'  # delimiter='/'
        if page_token:
            params += f'&pageToken={urllib.parse.quote(page_token, safe="")}'
        url = f'{_GCS_BASE}/{urllib.parse.quote(bucket, safe="")}/o?{params}'
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(req) as r:  # nosec B310
            body = json.load(r)
        for p in body.get('prefixes', []):
            # p looks like "1.2.3/" — strip trailing slash
            prefixes.append(p.rstrip('/'))
        page_token = body.get('nextPageToken')
        if not page_token:
            break
    return prefixes


def _latest_status(bucket: str, token: str, run_key: str) -> str | None:
    '''Return the most recent releaseStatus for run_key, or None if absent.'''
    prefix = f'{run_key}/.status-log/release/'
    url = (
        f'{_GCS_BASE}/{urllib.parse.quote(bucket, safe="")}/o'
        f'?prefix={urllib.parse.quote(prefix, safe="")}'
    )
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req) as r:  # nosec B310
        body = json.load(r)
    items = body.get('items', [])
    if not items:
        return None
    # lexicographically last name = latest timestamp
    latest = sorted(items, key=lambda i: i['name'])[-1]
    media_url = latest['mediaLink']
    req2 = urllib.request.Request(media_url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req2) as r:  # nosec B310
        payload = json.load(r)
    return payload.get('releaseStatus')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gcs-bucket', required=True, metavar='BUCKET')
    parser.add_argument('--gcs-token', required=True, metavar='TOKEN')
    parser.add_argument(
        '--limit',
        type=int,
        default=20,
        metavar='N',
        help='maximum number of versions to return (default: 20)',
    )
    args = parser.parse_args()

    if not args.gcs_token.strip():
        print('error: --gcs-token is empty', file=sys.stderr)
        sys.exit(1)

    all_prefixes = _list_prefixes(args.gcs_bucket, args.gcs_token)

    # separate semver-parseable from opaque run-keys
    semver_keys = []
    opaque_keys = []
    for p in all_prefixes:
        t = _normalise(p)
        if t is not None:
            semver_keys.append((t, p))
        else:
            opaque_keys.append(p)

    semver_keys.sort(key=lambda x: x[0], reverse=True)
    ordered = [p for _, p in semver_keys] + sorted(opaque_keys)
    limited = ordered[:args.limit]

    results = []
    for run_key in limited:
        try:
            status = _latest_status(args.gcs_bucket, args.gcs_token, run_key)
        except Exception as e:
            print(f'warning: could not read status for {run_key!r}: {e}', file=sys.stderr)
            status = None
        results.append({'version': run_key, 'status': status})

    print(json.dumps(results))


if __name__ == '__main__':
    main()
