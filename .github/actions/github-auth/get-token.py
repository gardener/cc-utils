#!/usr/bin/env python3
import argparse
import json
import os
import ssl
import sys
import time
import urllib3
import urllib3.exceptions


def _make_pool() -> urllib3.PoolManager:
    # Explicitly load system CA bundle so custom CAs installed on the runner are trusted.
    # Python may otherwise use a bundled store that predates any runner-installed certs.
    cafile = (
        os.environ.get('SSL_CERT_FILE')
        or ssl.get_default_verify_paths().cafile
        or '/etc/ssl/certs/ca-certificates.crt'
    )
    return urllib3.PoolManager(ssl_context=ssl.create_default_context(cafile=cafile))


_pool = _make_pool()


def _is_transient_status(status: int) -> bool:
    return status >= 500 or status in (408, 429)


def _request_with_retry(
    method: str,
    url: str,
    headers: dict | None = None,
    body: bytes | None = None,
    connect_timeout: int = 10,
    read_timeout: int = 30,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> urllib3.HTTPResponse:
    '''Send an HTTP request, retrying on transient errors with exponential backoff.

    Transient errors (5xx, 408, 429, network failures) are retried up to `retries` times.
    Non-transient 4xx responses (except 401) cause an immediate exit.
    2xx and 401 responses are returned to the caller.
    '''
    timeout = urllib3.Timeout(connect=connect_timeout, read=read_timeout)
    for attempt in range(retries + 1):
        try:
            resp = _pool.request(
                method,
                url,
                headers=headers or {},
                body=body,
                timeout=timeout,
            )
        except urllib3.exceptions.ConnectTimeoutError:
            reason = f'tcp connect timeout after {connect_timeout}s'
            if attempt == retries:
                print(f'ERROR: Request to {url} failed: {reason}', file=sys.stderr)
                sys.exit(1)
            delay = min(backoff_base ** (attempt + 1), backoff_cap)
            print(
                f'WARNING: Request to {url} failed: {reason}, '
                f'retrying in {delay:.0f}s ({attempt + 1}/{retries})...',
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        except urllib3.exceptions.ReadTimeoutError:
            reason = f'http read timeout after {read_timeout}s'
            if attempt == retries:
                print(f'ERROR: Request to {url} failed: {reason}', file=sys.stderr)
                sys.exit(1)
            delay = min(backoff_base ** (attempt + 1), backoff_cap)
            print(
                f'WARNING: Request to {url} failed: {reason}, '
                f'retrying in {delay:.0f}s ({attempt + 1}/{retries})...',
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        except urllib3.exceptions.RequestError as e:
            if attempt == retries:
                print(f'ERROR: Request to {url} failed: {e}', file=sys.stderr)
                sys.exit(1)
            delay = min(backoff_base ** (attempt + 1), backoff_cap)
            print(
                f'WARNING: Request to {url} failed: {e}, '
                f'retrying in {delay:.0f}s ({attempt + 1}/{retries})...',
                file=sys.stderr,
            )
            time.sleep(delay)
            continue

        if resp.status < 400 or resp.status == 401:
            return resp

        if not _is_transient_status(resp.status):
            print(f'ERROR: HTTP {resp.status} from {url}:\n{resp.data.decode()}', file=sys.stderr)
            sys.exit(1)

        if attempt == retries:
            print(
                f'ERROR: HTTP {resp.status} from {url}'
                f' (gave up after {retries} retries):\n{resp.data.decode()}',
                file=sys.stderr,
            )
            sys.exit(1)

        delay = min(backoff_base ** (attempt + 1), backoff_cap)
        print(
            f'WARNING: HTTP {resp.status} from {url}, '
            f'retrying in {delay:.0f}s ({attempt + 1}/{retries})...',
            file=sys.stderr,
        )
        time.sleep(delay)


def http_get(
    url: str,
    headers: dict | None = None,
    connect_timeout: int = 10,
    read_timeout: int = 30,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> dict:
    resp = _request_with_retry(
        'GET',
        url,
        headers=headers,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        backoff_base=backoff_base,
        backoff_cap=backoff_cap,
    )
    if resp.status != 200:
        print(f'ERROR: HTTP {resp.status} from {url}:\n{resp.data.decode()}', file=sys.stderr)
        sys.exit(1)
    return json.loads(resp.data.decode())


def get_oidc_token(
    request_url: str,
    request_token: str,
    audience: str,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> str:
    url = f'{request_url}&audience={audience}'
    data = http_get(
        url,
        headers={'Authorization': f'Bearer {request_token}'},
        retries=retries,
        backoff_base=backoff_base,
        backoff_cap=backoff_cap,
    )
    return data['value']


def exchange_token(
    token_server: str,
    host: str,
    organization: str,
    repositories: str,
    permissions: str,
    request_url: str,
    request_token: str,
    audience: str,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> str:
    payload = {
        'host': host,
        'organization': organization,
        'repositories': json.loads(repositories),
        'permissions': json.loads(permissions),
    }
    url = f'{token_server}/token-exchange'
    retry_kwargs = {'retries': retries, 'backoff_base': backoff_base, 'backoff_cap': backoff_cap}

    for oidc_attempt in range(2):
        payload['token'] = get_oidc_token(request_url, request_token, audience, **retry_kwargs)
        time.sleep(1)  # ensure token's iat is not in the future
        print(f'Payload: {json.dumps(payload)}', file=sys.stderr)
        resp = _request_with_retry(
            'POST',
            url,
            headers={'Content-Type': 'application/json'},
            body=json.dumps(payload).encode(),
            **retry_kwargs,
        )
        if resp.status == 200:
            return json.loads(resp.data.decode())['token']
        if resp.status == 401:
            if oidc_attempt == 1:
                print(
                    'ERROR: token exchange returned 401 after re-fetching OIDC token',
                    file=sys.stderr,
                )
                sys.exit(1)
            print(
                'WARNING: 401 on token exchange — OIDC token likely expired, re-fetching...',
                file=sys.stderr,
            )
            continue
        sys.exit(1)  # non-transient 4xx: _request_with_retry already exited; unreachable


def require_env(
    name: str,
    hint: str | None = None,
) -> str:
    val = os.environ.get(name, '')
    if not val:
        msg = f'ERROR: {name} is not set'
        if hint:
            msg += f'\n{hint}'
        print(msg, file=sys.stderr)
        sys.exit(1)
    return val


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token-server', required=True)
    parser.add_argument('--audience', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--organization', required=True)
    parser.add_argument('--repositories', required=True)
    parser.add_argument('--permissions', required=True)
    parser.add_argument('--retries', type=int, default=6)
    parser.add_argument('--backoff-base', type=float, default=3.0)
    parser.add_argument('--backoff-cap', type=float, default=60.0)
    args = parser.parse_args()

    oidc_hint = 'That typically means this workflow was not run with `id-token: write`-permission'
    request_url = require_env('ACTIONS_ID_TOKEN_REQUEST_URL', oidc_hint)
    request_token = require_env('ACTIONS_ID_TOKEN_REQUEST_TOKEN', oidc_hint)

    token_server = args.token_server
    if '://' not in token_server:
        token_server = f'https://{token_server}'

    token = exchange_token(
        token_server,
        args.host,
        args.organization,
        args.repositories,
        args.permissions,
        request_url=request_url,
        request_token=request_token,
        audience=args.audience,
        retries=args.retries,
        backoff_base=args.backoff_base,
        backoff_cap=args.backoff_cap,
    )

    github_output = os.environ.get('GITHUB_OUTPUT', '')
    if not github_output:
        print('ERROR: GITHUB_OUTPUT is not set', file=sys.stderr)
        sys.exit(1)

    with open(github_output, 'a') as f:
        f.write(f'token={token}\n')


if __name__ == '__main__':
    main()
