import datetime
import threading
import time
import unittest.mock

import oci.auth as oa
import oci.client as oc


class _FakeOidcCfg:
    audience = '//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/p'
    service_account = 'sa@project.iam.gserviceaccount.com'


_GAR_REF = 'europe-docker.pkg.dev/project/repo/image:tag'
_OTHER_REF = 'docker.io/library/alpine:3'


# --- make_refreshable_gar_credentials_lookup ---------------------------------

def test_refreshable_lookup_returns_creds_for_gar(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://tok.example.com')
    monkeypatch.setattr(oa, 'exchange_gar_token', lambda **_: 'token-A')

    lookup = oa.make_refreshable_gar_credentials_lookup(oidc_cfg=_FakeOidcCfg())
    creds = lookup(image_reference=_GAR_REF, absent_ok=True)

    assert isinstance(creds, oa.OciBasicAuthCredentials)
    assert creds.username == 'oauth2accesstoken'
    assert creds.password == 'token-A'


def test_refreshable_lookup_returns_none_for_non_gar(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://tok.example.com')
    monkeypatch.setattr(oa, 'exchange_gar_token', lambda **_: 'token-A')

    lookup = oa.make_refreshable_gar_credentials_lookup(oidc_cfg=_FakeOidcCfg())
    creds = lookup(image_reference=_OTHER_REF, absent_ok=True)

    assert creds is None


def test_refreshable_lookup_no_refetch_while_valid(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://tok.example.com')
    call_count = {'n': 0}

    def fake_exchange(**_):
        call_count['n'] += 1
        return 'token-A'

    monkeypatch.setattr(oa, 'exchange_gar_token', fake_exchange)

    lookup = oa.make_refreshable_gar_credentials_lookup(oidc_cfg=_FakeOidcCfg())
    lookup(image_reference=_GAR_REF)
    lookup(image_reference=_GAR_REF)

    assert call_count['n'] == 1


def test_refreshable_lookup_refetches_after_margin(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://tok.example.com')
    call_count = {'n': 0}
    tokens = ['token-A', 'token-B']

    def fake_exchange(**_):
        t = tokens[call_count['n']]
        call_count['n'] += 1
        return t

    monkeypatch.setattr(oa, 'exchange_gar_token', fake_exchange)

    # use a large margin so the token is always "near-expiry" after first fetch
    lookup = oa.make_refreshable_gar_credentials_lookup(
        oidc_cfg=_FakeOidcCfg(),
        prefetch_margin_seconds=7200,  # > 3600 s token lifetime → always needs refresh
    )

    c1 = lookup(image_reference=_GAR_REF)
    c2 = lookup(image_reference=_GAR_REF)

    assert call_count['n'] == 2
    assert c1.password == 'token-A'
    assert c2.password == 'token-B'


def test_refreshable_lookup_invalidate_cache(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://tok.example.com')
    call_count = {'n': 0}

    def fake_exchange(**_):
        call_count['n'] += 1
        return f'token-{call_count["n"]}'

    monkeypatch.setattr(oa, 'exchange_gar_token', fake_exchange)

    lookup = oa.make_refreshable_gar_credentials_lookup(oidc_cfg=_FakeOidcCfg())
    c1 = lookup(image_reference=_GAR_REF)
    lookup.invalidate_cache()
    c2 = lookup(image_reference=_GAR_REF)

    assert call_count['n'] == 2
    assert c1.password == 'token-1'
    assert c2.password == 'token-2'


def test_refreshable_lookup_concurrent_single_fetch(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://tok.example.com')
    call_count = {'n': 0}

    def slow_exchange(**_):
        time.sleep(0.05)
        call_count['n'] += 1
        return 'token-shared'

    monkeypatch.setattr(oa, 'exchange_gar_token', slow_exchange)

    lookup = oa.make_refreshable_gar_credentials_lookup(oidc_cfg=_FakeOidcCfg())
    results = []
    errors = []

    def worker():
        try:
            creds = lookup(image_reference=_GAR_REF)
            results.append(creds.password)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert all(r == 'token-shared' for r in results)
    assert call_count['n'] == 1


# --- _authenticate 401 retry -------------------------------------------------

def _make_mock_session(responses):
    '''responses: list of (status_code, json_dict) applied in order'''
    session = unittest.mock.MagicMock()
    side_effects = []
    for status, body in responses:
        r = unittest.mock.MagicMock()
        r.ok = status < 400
        r.status_code = status
        r.reason = 'OK' if status < 400 else 'Unauthorized'
        r.content = b''
        r.headers = {
            'www-authenticate': (
                'Bearer realm="https://auth.example.com/token",'
                'service="registry.example.com",'
                'scope="repository:foo:pull"'
            ),
        }
        r.json.return_value = body
        side_effects.append(r)
    session.get.side_effect = side_effects
    return session


def test_authenticate_401_triggers_invalidate_and_retry(monkeypatch):
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'gh-tok')
    monkeypatch.setenv('ACTIONS_ID_TOKEN_REQUEST_URL', 'https://tok.example.com')

    exchange_calls = {'n': 0}
    tokens = ['token-stale', 'token-fresh']

    def fake_exchange(**_):
        t = tokens[exchange_calls['n']]
        exchange_calls['n'] += 1
        return t

    monkeypatch.setattr(oa, 'exchange_gar_token', fake_exchange)

    lookup = oa.make_refreshable_gar_credentials_lookup(oidc_cfg=_FakeOidcCfg())

    # /v2/ probe → bearer challenge
    probe_resp = unittest.mock.MagicMock()
    probe_resp.ok = True
    probe_resp.headers = {
        'www-authenticate': (
            'Bearer realm="https://auth.example.com/token",'
            'service="registry.example.com"'
        ),
    }

    # first bearer-realm call → 401 (stale token)
    auth_401 = unittest.mock.MagicMock()
    auth_401.ok = False
    auth_401.status_code = 401
    auth_401.reason = 'Unauthorized'
    auth_401.content = b'unauthorized'

    # second bearer-realm call → 200 (fresh token)
    valid_token_resp = unittest.mock.MagicMock()
    valid_token_resp.ok = True
    valid_token_resp.status_code = 200
    valid_token_resp.json.return_value = {
        'token': 'oci-bearer-token',
        'expires_in': 3600,
        'issued_at': datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        'scope': 'repository:project/repo/image:pull',
    }

    session = unittest.mock.MagicMock()
    # sequence: probe, auth-with-stale, probe-again, auth-with-fresh
    session.get.side_effect = [probe_resp, auth_401, probe_resp, valid_token_resp]

    client = oc.Client(credentials_lookup=lookup, session=session)

    client._authenticate(
        image_reference=_GAR_REF,
        scope='repository:project/repo/image:pull',
    )

    assert exchange_calls['n'] == 2
