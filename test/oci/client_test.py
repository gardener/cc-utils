import base64
import threading
import time
import unittest.mock

import requests

import oci.auth
import oci.client as co


def test_append_b64_padding_if_missing():
    def encode_and_decode(octets: bytes):
        encoded = base64.b64encode(octets).decode('utf-8')
        encoded_wo_padding = encoded.strip('=')

        assert encoded == co._append_b64_padding_if_missing(encoded_wo_padding)

    encode_and_decode(b'a')
    encode_and_decode(b'ab')
    encode_and_decode(b'abc')
    encode_and_decode(b'abcd')


def test_per_host_throttle_aimd():
    t = co._PerHostThrottle(host='test.example.com', initial_limit=8)
    assert t._limit == 8

    t.on_429()
    assert t._limit == 4

    t.on_429()
    assert t._limit == 2

    t.on_429()
    assert t._limit == 1

    # minimum is 1
    t.on_429()
    assert t._limit == 1

    # additive increase
    t.on_success()
    assert t._limit == 2

    # does not exceed initial
    t._limit = 7
    t.on_success()
    assert t._limit == 8
    t.on_success()
    assert t._limit == 8  # capped


def test_per_host_throttle_blocks_at_limit():
    t = co._PerHostThrottle(host='test.example.com', initial_limit=1)

    results = []

    with t:
        # throttle slot is held; a second acquire should block
        def try_acquire():
            with t:
                results.append('acquired')

        thread = threading.Thread(target=try_acquire)
        thread.start()
        # give thread a moment to block
        thread.join(timeout=0.1)
        assert not results, 'second thread should not have acquired while slot is held'

    thread.join(timeout=1)
    assert results == ['acquired'], 'second thread should acquire after first releases'


def test_per_host_throttle_resume_at_blocks():
    '''on_429 with retry_after > 0 prevents new __enter__ calls until the window elapses.'''
    t = co._PerHostThrottle(host='test.example.com', initial_limit=4)
    t.on_429(retry_after=0.15)

    acquired_at = []

    def acquire():
        with t:
            acquired_at.append(time.monotonic())

    before = time.monotonic()
    thread = threading.Thread(target=acquire)
    thread.start()
    thread.join(timeout=2.0)

    assert len(acquired_at) == 1
    assert acquired_at[0] - before >= 0.10, 'thread should have been blocked for at least ~0.1s'


def _mock_response(status_code, headers=None):
    r = unittest.mock.Mock(spec=requests.Response)
    r.status_code = status_code
    r.ok = (status_code < 400)
    r.reason = 'mock'
    r.content = b''
    r.headers = headers or {}
    return r


def test_client_calls_throttle_on_429():
    '''on_429() halves the limit on 429; on_success() recovers it after the retry succeeds.'''
    client = co.Client(max_write_concurrency_per_host=4)

    # pre-populate auth cache so _authenticate() exits early without network I/O
    client.token_cache.set_auth_method(
        image_reference='registry.example.com/repo:tag',
        auth_method=co.AuthMethod.BASIC,
    )

    call_count = 0

    def fake_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response(429, headers={'Retry-After': '0'})
        return _mock_response(200)

    with unittest.mock.patch.object(client.session, 'request', side_effect=fake_request):
        client._request(
            url='https://registry.example.com/v2/repo/blobs/uploads/',
            image_reference='registry.example.com/repo:tag',
            scope='repository:repo:push',
            method='POST',
        )

    throttle = client._host_write_throttles['registry.example.com']
    # 4 → 2 from on_429, then 2 → 3 from on_success on the successful retry
    assert throttle._limit == 3
    assert call_count == 2


# ---------------------------------------------------------------------------
# batch_delete_manifests
# ---------------------------------------------------------------------------

ECR_REF = '123456789012.dkr.ecr.eu-west-1.amazonaws.com/my-repo:latest'
ECR_REFS = [
    '123456789012.dkr.ecr.eu-west-1.amazonaws.com/my-repo:v1',
    '123456789012.dkr.ecr.eu-west-1.amazonaws.com/my-repo:v2',
    '123456789012.dkr.ecr.eu-west-1.amazonaws.com/my-repo@sha256:abcdef',
]


def test_batch_delete_manifests_rejects_non_aws():
    client = co.Client()
    raised = False
    try:
        client.batch_delete_manifests(
            image_reference='gcr.io/project/repo:latest',
            refs=[],
        )
    except ValueError:
        raised = True
    assert raised


def test_batch_delete_manifests_aws():
    mock_creds = oci.auth.OciAccessKeyCredentials(
        access_key_id='AKIATEST',
        secret_access_key='secret',
        session_token=None,
    )

    def mock_credentials_lookup(image_reference, privileges, absent_ok):
        return mock_creds

    client = co.Client(credentials_lookup=mock_credentials_lookup)

    with unittest.mock.patch('oci.aws.batch_delete_images', return_value=[]) as mock_batch:
        client.batch_delete_manifests(image_reference=ECR_REF, refs=ECR_REFS)

    mock_batch.assert_called_once()
    image_ids = mock_batch.call_args.kwargs['image_ids']
    assert {'imageTag': 'v1'} in image_ids
    assert {'imageTag': 'v2'} in image_ids
    assert {'imageDigest': 'sha256:abcdef'} in image_ids


def test_host_is_allowed():
    h = co.host_is_allowed
    # same host
    assert h('https://eu.gcr.io/v2/x', 'eu.gcr.io')
    # default-port normalisation
    assert h('https://eu.gcr.io:443/v2/x', 'eu.gcr.io')
    assert h('https://eu.gcr.io/v2/x', 'eu.gcr.io:443')
    # cross-host
    assert not h('https://attacker.example/x', 'eu.gcr.io')
    # subdomain is NOT the same host
    assert not h('https://eu.gcr.io.attacker.example/x', 'eu.gcr.io')
    # docker-hub split-host token service
    assert h('https://auth.docker.io/token', 'registry-1.docker.io')
    assert not h('https://auth.docker.io/token', 'eu.gcr.io')
    # public ECR realm is same-host
    assert h('https://public.ecr.aws/token/', 'public.ecr.aws')
    # scheme: http refused unless insecure opt-in
    assert not h('http://eu.gcr.io/v2/x', 'eu.gcr.io')
    assert h('http://eu.gcr.io/v2/x', 'eu.gcr.io', disable_tls_validation=True)


def test_require_same_registry_host_raises():
    import pytest
    with pytest.raises(ValueError):
        co.require_same_registry_host('https://attacker.example/x', 'eu.gcr.io', 'realm')
    # allowed -> no raise
    co.require_same_registry_host('https://eu.gcr.io/v2/x', 'eu.gcr.io', 'realm')


def _client_for_realm_test():
    return co.Client(
        credentials_lookup=lambda **kw: None,
        routes=co.OciRoutes(),
    )


def test_authenticate_refuses_cross_host_realm():
    client = _client_for_realm_test()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        # first GET (/v2/) returns a bearer challenge whose realm is on a foreign host
        return _mock_response(
            401,
            headers={'www-authenticate': 'Bearer realm="https://attacker.example/token",service="x"'},
        )

    with unittest.mock.patch.object(client.session, 'get', side_effect=fake_get):
        try:
            client._authenticate(image_reference='eu.gcr.io/foo/bar:1', scope='repository:foo/bar:pull')
            raise AssertionError('expected ValueError for cross-host realm')
        except ValueError:
            pass

    # the foreign realm URL must never have been requested
    assert not any('attacker.example' in u for u in calls)


def test_authenticate_allows_docker_hub_split_host_realm():
    client = _client_for_realm_test()

    def fake_get(url, **kwargs):
        if url.endswith('/v2/'):
            return _mock_response(
                401,
                headers={'www-authenticate': 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'},
            )
        # the realm GET (auth.docker.io) is allowed and returns a token
        return _mock_response(200)

    client._mock_token_payload = True
    with unittest.mock.patch.object(client.session, 'get', side_effect=fake_get):
        # registry-1.docker.io image → realm auth.docker.io is on the allowlist; must not raise
        # (token parsing may fail on the empty 200 body, so we only assert no ValueError from the host check)
        try:
            client._authenticate(image_reference='registry-1.docker.io/library/alpine:3', scope='repository:library/alpine:pull')
        except ValueError as e:
            if 'refusing to send credentials' in str(e):
                raise AssertionError('docker-hub split-host realm was wrongly refused') from e
        except Exception:
            pass  # token/json parsing issues on the mock 200 are not what this test checks


def test_put_blob_single_post_refuses_cross_host_location():
    client = _client_for_realm_test()
    seen_methods = []

    def fake_request(url, image_reference, scope, method='GET', **kwargs):
        seen_methods.append(method)
        if method == 'POST':
            r = _mock_response(202, headers={'Location': 'https://attacker.example/upload/abc'})
            r.url = 'https://eu.gcr.io/v2/foo/blobs/uploads/'
            return r
        return _mock_response(201)

    with unittest.mock.patch.object(client, '_request', side_effect=fake_request):
        try:
            client._put_blob_single_post(
                image_reference='eu.gcr.io/foo:1',
                digest='sha256:' + 'a' * 64,
                octets_count=3,
                data=b'abc',
            )
            raise AssertionError('expected ValueError for cross-host upload Location')
        except ValueError:
            pass

    # the PUT (second _request) must never have fired
    assert 'PUT' not in seen_methods


def test_next_paginated_url_stops_on_cross_host():
    u = co._next_paginated_url
    hdr_cross = {'Link': '<https://attacker.example/v2/_tags?n=1&last=x>; rel="next"'}
    assert u('https://eu.gcr.io/v2/x/tags/list', hdr_cross, 'eu.gcr.io') is None
    hdr_same = {'Link': '<https://eu.gcr.io/v2/x/tags/list?last=x>; rel="next"'}
    assert u('https://eu.gcr.io/v2/x/tags/list', hdr_same, 'eu.gcr.io') is not None
    # relative next stays same-host
    hdr_rel = {'Link': '</v2/x/tags/list?last=x>; rel="next"'}
    assert u('https://eu.gcr.io/v2/x/tags/list', hdr_rel, 'eu.gcr.io').startswith('https://eu.gcr.io/')
