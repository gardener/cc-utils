# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
import unittest.mock as mock

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '.github', 'actions', 'kubernetes-auth',
        )
    ),
)

import kubernetes_auth


# --- build_kubeconfig ---

def test_build_kubeconfig_structure():
    cfg = kubernetes_auth.build_kubeconfig(
        server='https://k8s.example.com',
        server_ca='AABBCC==',
        token='my-token',
    )
    assert cfg['apiVersion'] == 'v1'
    assert cfg['kind'] == 'Config'
    assert cfg['current-context'] == 'gha'


def test_build_kubeconfig_server_and_ca():
    cfg = kubernetes_auth.build_kubeconfig(
        server='https://k8s.example.com',
        server_ca='AABBCC==',
        token='tok',
    )
    cluster = cfg['clusters'][0]['cluster']
    assert cluster['server'] == 'https://k8s.example.com'
    assert cluster['certificate-authority-data'] == 'AABBCC=='


def test_build_kubeconfig_token():
    cfg = kubernetes_auth.build_kubeconfig(
        server='https://k8s.example.com',
        server_ca='ca',
        token='secret-token',
    )
    assert cfg['users'][0]['user']['token'] == 'secret-token'


# --- fetch_gha_oidc_token ---

def test_fetch_gha_oidc_token(monkeypatch):
    response_body = json.dumps({'value': 'gha-oidc-token'}).encode()

    cm = mock.MagicMock()
    cm.__enter__ = mock.MagicMock(return_value=cm)
    cm.__exit__ = mock.MagicMock(return_value=False)
    cm.read.return_value = response_body

    monkeypatch.setattr(
        kubernetes_auth.urllib.request,
        'urlopen',
        mock.MagicMock(return_value=cm),
    )

    token = kubernetes_auth.fetch_gha_oidc_token(
        token_request_url='https://token.actions.githubusercontent.com?a=b',
        bearer_token='bearer',
        audience='gardener',
    )
    assert token == 'gha-oidc-token'


def test_fetch_gha_oidc_token_url_includes_audience(monkeypatch):
    captured = {}

    response_body = json.dumps({'value': 'tok'}).encode()
    cm = mock.MagicMock()
    cm.__enter__ = mock.MagicMock(return_value=cm)
    cm.__exit__ = mock.MagicMock(return_value=False)
    cm.read.return_value = response_body

    def fake_urlopen(req, **kwargs):
        captured['url'] = req.full_url
        return cm

    monkeypatch.setattr(kubernetes_auth.urllib.request, 'urlopen', fake_urlopen)

    kubernetes_auth.fetch_gha_oidc_token(
        token_request_url='https://token.example.com?base=1',
        bearer_token='b',
        audience='my-audience',
    )
    assert 'audience=my-audience' in captured['url']


# --- fetch_sa_token ---

def test_fetch_sa_token(monkeypatch):
    import base64
    # minimal self-signed-like PEM (content doesn't matter — we mock urlopen)
    fake_ca_b64 = base64.b64encode(b'fake-pem').decode()

    response_body = json.dumps({'status': {'token': 'sa-token'}}).encode()
    cm = mock.MagicMock()
    cm.__enter__ = mock.MagicMock(return_value=cm)
    cm.__exit__ = mock.MagicMock(return_value=False)
    cm.read.return_value = response_body

    monkeypatch.setattr(
        kubernetes_auth.urllib.request,
        'urlopen',
        mock.MagicMock(return_value=cm),
    )
    # also patch ssl.SSLContext so we don't need a real CA
    mock_ctx = mock.MagicMock()
    monkeypatch.setattr(kubernetes_auth.ssl, 'SSLContext', mock.MagicMock(return_value=mock_ctx))

    token = kubernetes_auth.fetch_sa_token(
        server='https://k8s.example.com',
        server_ca_b64=fake_ca_b64,
        gh_token='gh-tok',
        namespace='default',
        sa_name='my-sa',
        expiration=3600,
    )
    assert token == 'sa-token'
