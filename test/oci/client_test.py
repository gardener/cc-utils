import base64
import threading
import time
import unittest.mock

import requests

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
    client = co.Client(max_concurrent_per_host=4)

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

    throttle = co._PerHostThrottle(host='registry.example.com', initial_limit=4)
    client._throttle_for = lambda host: throttle

    with unittest.mock.patch.object(client.session, 'request', side_effect=fake_request):
        with unittest.mock.patch('oci.client.time.sleep'):
            client._request(
                url='https://registry.example.com/v2/repo/blobs/uploads/',
                image_reference='registry.example.com/repo:tag',
                scope='repository:repo:push',
                method='POST',
            )

    # 4 → 2 from on_429, then 2 → 3 from on_success on the successful retry
    assert throttle._limit == 3
    assert call_count == 2
