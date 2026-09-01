import datetime
import threading
import time
import unittest.mock

import oci.auth as oa
import oci.client as oc

_GAR_OID_CFG = oa.GarOidcConfiguration(
    audience='//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/p/providers/p',
    service_account='sa@project.iam.gserviceaccount.com',
)

_GAR_REF = 'europe-docker.pkg.dev/project/repo/image:tag'
_OTHER_REF = 'docker.io/library/alpine:3'


def _lookup_stubbing_exchange(
    monkeypatch,
    exchange_impl,
    prefetch_margin_seconds: int=300,
) -> oa._RefreshableGarCredentialsLookup:
    '''
    Build a refreshable_gar_credentials_lookup whose subject-token is static and whose
    exchange_gar_token is replaced by `exchange_impl(subject-token-invariant)`.
    '''
    monkeypatch.setattr(
        oa,
        'exchange_gar_token',
        lambda oidc_cfg, subject_token, lifetime_seconds=3600: exchange_impl(),
    )

    return oa.refreshable_gar_credentials_lookup(
        oidc_cfg=_GAR_OID_CFG,
        subject_token_supplier=lambda: 'subject-token',
        prefetch_margin_seconds=prefetch_margin_seconds,
    )


# --- refreshable_gar_credentials_lookup ---------------------------------------

def test_refreshable_lookup_returns_creds_for_gar(monkeypatch):
    lookup = _lookup_stubbing_exchange(monkeypatch, lambda: 'token-A')

    creds = lookup(image_reference=_GAR_REF, absent_ok=True)

    assert isinstance(creds, oa.OciBasicAuthCredentials)
    assert creds.username == 'oauth2accesstoken'
    assert creds.password == 'token-A'


def test_refreshable_lookup_returns_none_for_non_gar(monkeypatch):
    lookup = _lookup_stubbing_exchange(monkeypatch, lambda: 'token-A')

    creds = lookup(image_reference=_OTHER_REF, absent_ok=True)

    assert creds is None


def test_refreshable_lookup_no_refetch_while_valid(monkeypatch):
    call_count = {'n': 0}

    def fake_exchange():
        call_count['n'] += 1
        return 'token-A'

    lookup = _lookup_stubbing_exchange(monkeypatch, fake_exchange)
    lookup(image_reference=_GAR_REF)
    lookup(image_reference=_GAR_REF)

    assert call_count['n'] == 1


def test_refreshable_lookup_refetches_after_margin(monkeypatch):
    call_count = {'n': 0}
    tokens = ['token-A', 'token-B']

    def fake_exchange():
        token = tokens[call_count['n']]
        call_count['n'] += 1
        return token

    # use a large margin so the token is always "near-expiry" after first fetch
    lookup = _lookup_stubbing_exchange(
        monkeypatch,
        fake_exchange,
        prefetch_margin_seconds=7200,  # > 3600 s token lifetime → always needs refresh
    )

    c1 = lookup(image_reference=_GAR_REF)
    c2 = lookup(image_reference=_GAR_REF)

    assert call_count['n'] == 2
    assert c1.password == 'token-A'
    assert c2.password == 'token-B'


def test_refreshable_lookup_invalidate_cache(monkeypatch):
    call_count = {'n': 0}

    def fake_exchange():
        call_count['n'] += 1
        return f'token-{call_count["n"]}'

    lookup = _lookup_stubbing_exchange(monkeypatch, fake_exchange)
    c1 = lookup(image_reference=_GAR_REF)
    lookup.invalidate_cache()
    c2 = lookup(image_reference=_GAR_REF)

    assert call_count['n'] == 2
    assert c1.password == 'token-1'
    assert c2.password == 'token-2'


def test_refreshable_lookup_concurrent_single_fetch(monkeypatch):
    call_count = {'n': 0}

    def slow_exchange():
        time.sleep(0.05)
        call_count['n'] += 1
        return 'token-shared'

    lookup = _lookup_stubbing_exchange(monkeypatch, slow_exchange)
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


# --- _authenticate 401 retry --------------------------------------------------

def test_authenticate_401_triggers_invalidate_and_retry(monkeypatch):
    exchange_calls = {'n': 0}
    tokens = ['token-stale', 'token-fresh']

    def fake_exchange():
        token = tokens[exchange_calls['n']]
        exchange_calls['n'] += 1
        return token

    lookup = _lookup_stubbing_exchange(monkeypatch, fake_exchange)

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

    client = oc.Client(
        credentials_lookup=lookup,
        credentials_invalidate=lookup.invalidate_cache,
        session=session,
    )

    client._authenticate(
        image_reference=_GAR_REF,
        scope='repository:project/repo/image:pull',
    )

    assert exchange_calls['n'] == 2
