#!/usr/bin/env python3
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request


class _MethodPreservingRedirectHandler(urllib.request.HTTPRedirectHandler):
    '''Follow 307/308 redirects preserving method and body (urllib drops both by default).'''
    def http_error_307(self, req, fp, code, msg, headers):
        new_req = urllib.request.Request(
            headers['Location'],
            data=req.data,
            headers=req.headers,
            method=req.get_method(),
        )
        return self.parent.open(new_req)

    http_error_308 = http_error_307


def _ssl_ctx() -> ssl.SSLContext:
    # Explicitly load system CA bundle so custom CAs installed on the runner are trusted.
    # Python may otherwise use a bundled store that predates any runner-installed certs.
    cafile = (
        os.environ.get('SSL_CERT_FILE')
        or ssl.get_default_verify_paths().cafile
        or '/etc/ssl/certs/ca-certificates.crt'
    )
    return ssl.create_default_context(cafile=cafile)


_opener = urllib.request.build_opener(
    _MethodPreservingRedirectHandler,
    urllib.request.HTTPSHandler(context=_ssl_ctx()),
)


def _open_with_retry(
    req: urllib.request.Request,
    timeout_seconds: int = 30,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> dict:
    '''Open a request, retrying on transient errors with exponential backoff.

    4xx responses are treated as systematic failures and are not retried.
    '''
    url = req.full_url
    for attempt in range(retries + 1):
        try:
            with _opener.open(req, timeout=timeout_seconds) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code < 500 and e.code not in (408, 429):
                # 4xx (except 408/429): systematic – retrying won't help
                print(f'ERROR: HTTP {e.code} from {url}:\n{body}', file=sys.stderr)
                sys.exit(1)
            if attempt == retries:
                print(
                    f'ERROR: HTTP {e.code} from {url} (gave up after {retries} retries):\n{body}',
                    file=sys.stderr,
                )
                sys.exit(1)
            delay = min(backoff_base ** attempt, backoff_cap)
            print(
                f'WARNING: HTTP {e.code} from {url}, '
                f'retrying in {delay:.0f}s ({attempt + 1}/{retries})...',
                file=sys.stderr,
            )
            time.sleep(delay)
        except urllib.error.URLError as e:
            if attempt == retries:
                print(f'ERROR: Request to {url} failed: {e.reason}', file=sys.stderr)
                sys.exit(1)
            delay = min(backoff_base ** attempt, backoff_cap)
            print(
                f'WARNING: Request to {url} failed: {e.reason}, '
                f'retrying in {delay:.0f}s ({attempt + 1}/{retries})...',
                file=sys.stderr,
            )
            time.sleep(delay)


def http_get(
    url: str,
    headers: dict | None = None,
    timeout_seconds: int = 30,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    return _open_with_retry(req, timeout_seconds, retries, backoff_base, backoff_cap)


def http_post(
    url: str,
    payload: dict,
    timeout_seconds: int = 30,
    retries: int = 6,
    backoff_base: float = 3.0,
    backoff_cap: float = 60.0,
) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
    )
    return _open_with_retry(req, timeout_seconds, retries, backoff_base, backoff_cap)


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
