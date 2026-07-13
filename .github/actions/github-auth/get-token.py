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
) -> dict:
    '''Send an HTTP request, retrying on transient errors with exponential backoff.

    TCP connect timeouts are not retried: if the connection cannot be established,
    the runner is likely blocked by a firewall and retrying on the same runner is futile.
    4xx responses (except 408/429) are also not retried.
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
            print(
                f'ERROR: Request to {url} failed: tcp connect timeout after {connect_timeout}s'
                f' - likely a firewall/network issue on this runner, retrying is futile',
                file=sys.stderr,
            )
            sys.exit(1)
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

        if resp.status >= 400:
            body_str = resp.data.decode()
            if resp.status < 500 and resp.status not in (408, 429):
                # 4xx (except 408/429): systematic – retrying won't help
                print(f'ERROR: HTTP {resp.status} from {url}:\n{body_str}', file=sys.stderr)
                sys.exit(1)
            if attempt == retries:
                print(
                    f'ERROR: HTTP {resp.status} from {url}'
                    f' (gave up after {retries} retries):\n{body_str}',
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
            continue

        return json.loads(resp.data.decode())


def http_get(
    url: str,
    headers: dict | None = None,
    connect_timeout: int = 10,
    read_timeout: int = 30,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> dict:
    return _request_with_retry(
        'GET',
        url,
        headers=headers,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        backoff_base=backoff_base,
        backoff_cap=backoff_cap,
    )


def http_post(
    url: str,
    payload: dict,
    connect_timeout: int = 10,
    read_timeout: int = 30,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> dict:
    return _request_with_retry(
        'POST',
        url,
        headers={'Content-Type': 'application/json'},
        body=json.dumps(payload).encode(),
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries=retries,
        backoff_base=backoff_base,
        backoff_cap=backoff_cap,
    )


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
    id_token: str,
    repositories: str,
    permissions: str,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> str:
    time.sleep(1)  # ensure token's iat is not in the future
    payload = {
        'host': host,
        'organization': organization,
        'token': id_token,
        'repositories': json.loads(repositories),
        'permissions': json.loads(permissions),
    }
    print(f'Payload: {json.dumps(payload)}')
    data = http_post(
        f'{token_server}/token-exchange',
        payload,
        retries=retries,
        backoff_base=backoff_base,
        backoff_cap=backoff_cap,
    )
    return data['token']


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

    retry_kwargs = {
        'retries': args.retries,
        'backoff_base': args.backoff_base,
        'backoff_cap': args.backoff_cap,
    }

    id_token = get_oidc_token(request_url, request_token, args.audience, **retry_kwargs)
    token = exchange_token(
        token_server,
        args.host,
        args.organization,
        id_token,
        args.repositories,
        args.permissions,
        **retry_kwargs,
    )

    github_output = os.environ.get('GITHUB_OUTPUT', '')
    if not github_output:
        print('ERROR: GITHUB_OUTPUT is not set', file=sys.stderr)
        sys.exit(1)

    with open(github_output, 'a') as f:
        f.write(f'token={token}\n')


if __name__ == '__main__':
    main()
