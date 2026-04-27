#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

'''
Kubernetes OIDC authentication helper for GitHub Actions.

Fetches a GHA OIDC token, optionally exchanges it for a Kubernetes
service-account token, and writes a kubeconfig.

Environment variables consumed (when run as __main__):
  ACTIONS_ID_TOKEN_REQUEST_TOKEN  GHA OIDC request token
  ACTIONS_ID_TOKEN_REQUEST_URL    GHA OIDC request URL
  INPUT_SERVER                    k8s API server URL
  INPUT_SERVER_CA                 base64-encoded CA bundle (optional)
  PREP_SERVER_CA                  CA bundle from prepare-server-ca step (fallback)
  INPUT_AUDIENCE                  OIDC audience (default: gardener)
  INPUT_SERVICE_ACCOUNT_NAME      SA name (optional)
  INPUT_SERVICE_ACCOUNT_NAMESPACE SA namespace (optional)
  INPUT_SERVICE_ACCOUNT_TOKEN_EXPIRATION  expiration seconds (default: 3600)
  INPUT_KUBECONFIG_PATH           output path (default: kubeconfig.yaml)
  GITHUB_OUTPUT                   GHA output file
'''

import json
import ssl
import urllib.request


def build_kubeconfig(server: str, server_ca: str, token: str) -> dict:
    return {
        'apiVersion': 'v1',
        'clusters': [{
            'name': 'cluster',
            'cluster': {
                'server': server,
                'certificate-authority-data': server_ca,
            },
        }],
        'contexts': [{'name': 'gha', 'context': {'cluster': 'cluster', 'user': 'gha'}}],
        'current-context': 'gha',
        'kind': 'Config',
        'preferences': {},
        'users': [{'name': 'gha', 'user': {'token': token}}],
    }


def fetch_gha_oidc_token(token_request_url: str, bearer_token: str, audience: str) -> str:
    url = f'{token_request_url}&audience={audience}'
    req = urllib.request.Request(
        url,
        headers={'Authorization': f'Bearer {bearer_token}'},
    )
    with urllib.request.urlopen(req) as resp:  # nosec B310
        return json.loads(resp.read())['value']


def fetch_sa_token(
    server: str,
    server_ca_b64: str,
    gh_token: str,
    namespace: str,
    sa_name: str,
    expiration: int = 3600,
) -> str:
    import base64
    ca_pem = base64.b64decode(server_ca_b64).decode('ascii')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=ca_pem)

    url = (
        f'{server}/api/v1/namespaces/{namespace}'
        f'/serviceaccounts/{sa_name}/token'
    )
    body = json.dumps({
        'apiVersion': 'authentication.k8s.io/v1',
        'kind': 'TokenRequest',
        'spec': {
            'audiences': ['kubernetes', 'gardener'],
            'expirationSeconds': expiration,
        },
    }).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={
            'Authorization': f'Bearer {gh_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(req, context=ctx) as resp:  # nosec B310
        return json.loads(resp.read())['status']['token']


if __name__ == '__main__':
    import os
    import sys
    import yaml

    gh_token = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN', '')
    token_url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL', '')

    if not gh_token or not token_url:
        print('Error: ACTIONS_ID_TOKEN_REQUEST_TOKEN and/or ACTIONS_ID_TOKEN_REQUEST_URL not set')
        print('that typically means this workflow was not run with `id-token: write` permission')
        sys.exit(1)

    server = os.environ['INPUT_SERVER']
    server_ca = os.environ.get('INPUT_SERVER_CA') or os.environ.get('PREP_SERVER_CA', '')
    audience = os.environ.get('INPUT_AUDIENCE', 'gardener')
    sa_name = os.environ.get('INPUT_SERVICE_ACCOUNT_NAME', '')
    sa_namespace = os.environ.get('INPUT_SERVICE_ACCOUNT_NAMESPACE', '')
    sa_expiration = int(os.environ.get('INPUT_SERVICE_ACCOUNT_TOKEN_EXPIRATION', '3600'))
    kubeconfig_path = os.environ.get('INPUT_KUBECONFIG_PATH', 'kubeconfig.yaml')

    auth_token = fetch_gha_oidc_token(
        token_request_url=token_url,
        bearer_token=gh_token,
        audience=audience,
    )
    print('successfully retrieved a gh-auth-token')

    if sa_name and sa_namespace:
        print('service-account details specified, requesting a service-account-token')
        auth_token = fetch_sa_token(
            server=server,
            server_ca_b64=server_ca,
            gh_token=auth_token,
            namespace=sa_namespace,
            sa_name=sa_name,
            expiration=sa_expiration,
        )
        print('successfully retrieved a service account auth-token')

    kubeconfig = build_kubeconfig(server=server, server_ca=server_ca, token=auth_token)
    kubeconfig_yaml = yaml.dump(kubeconfig, default_flow_style=False)

    if kubeconfig_path:
        with open(kubeconfig_path, 'w') as f:
            f.write(kubeconfig_yaml)
        print(f'kubeconfig written to {kubeconfig_path}')

    with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
        f.write(f'kubeconfig<<EOF\n{kubeconfig_yaml}EOF\n')
