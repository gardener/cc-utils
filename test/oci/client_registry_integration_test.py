'''
Integration test: the OCI client against a real, spec-conformant registry
(the official CNCF distribution registry, `registry:2`) over TLS.

This exercises the real HTTP round-trip (not mocked responses) and asserts that
the credential-forwarding host boundary added for the Glasswing credential-
forwarding finding does NOT break the normal same-host push/pull flow — the
registry's blob-upload `Location` is same-host, so the guard must let it through.

Note: `registry:2` is the OCI Distribution reference implementation, not one of
the registries Gardener runs in production. It proves the guard leaves the
standard distribution-spec flow intact; it does not cover vendor-specific
split-host token realms (Docker Hub -> auth.docker.io, etc.). Those are covered
by the unit tests and belong in an end-to-end test against the real registries.

The test is skipped unless a docker daemon is reachable.
'''

import hashlib
import os
import shutil
import subprocess
import tempfile
import time

import pytest
import requests

import oci.client as oc


_REGISTRY_IMAGE = 'registry:2'
_HOST_PORT = 5999
_CONTAINER_NAME = 'ccutils-oci-itest-registry'


def _docker_available() -> bool:
    if not shutil.which('docker'):
        return False
    try:
        return subprocess.run(
            ['docker', 'info'],
            capture_output=True,
            timeout=10,
        ).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason='docker daemon not available',
)


@pytest.fixture(scope='module')
def tls_registry():
    '''
    Starts a throwaway `registry:2` served over TLS with a self-signed cert for 127.0.0.1,
    yields (base_host, ca_cert_path), and tears the container down afterwards.
    '''
    certdir = tempfile.mkdtemp(prefix='ccutils-oci-itest-certs.')
    crt = os.path.join(certdir, 'domain.crt')
    key = os.path.join(certdir, 'domain.key')
    subprocess.run(
        [
            'openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', key,
            '-x509', '-days', '1', '-out', crt,
            '-subj', '/CN=127.0.0.1',
            '-addext', 'subjectAltName=IP:127.0.0.1',
        ],
        check=True,
        capture_output=True,
    )

    subprocess.run(['docker', 'rm', '-f', _CONTAINER_NAME], capture_output=True)
    subprocess.run(
        [
            'docker', 'run', '-d', '--name', _CONTAINER_NAME,
            '-p', f'{_HOST_PORT}:5000',
            '-v', f'{certdir}:/certs',
            '-e', 'REGISTRY_HTTP_TLS_CERTIFICATE=/certs/domain.crt',
            '-e', 'REGISTRY_HTTP_TLS_KEY=/certs/domain.key',
            _REGISTRY_IMAGE,
        ],
        check=True,
        capture_output=True,
    )

    host = f'127.0.0.1:{_HOST_PORT}'
    # wait for the registry to accept requests
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if requests.get(f'https://{host}/v2/', verify=crt, timeout=2).status_code in (200, 401):
                break
        except requests.exceptions.RequestException:
            time.sleep(0.5)
    else:
        subprocess.run(['docker', 'rm', '-f', _CONTAINER_NAME], capture_output=True)
        pytest.fail('registry did not become ready in time')

    try:
        yield host, crt
    finally:
        subprocess.run(['docker', 'rm', '-f', _CONTAINER_NAME], capture_output=True)
        shutil.rmtree(certdir, ignore_errors=True)


def _client(ca_cert_path: str) -> oc.Client:
    # requests honours REQUESTS_CA_BUNDLE as the default CA; the client sets verify=True
    # explicitly on some requests, so pointing the bundle at our self-signed cert is the
    # reliable way to make verification succeed against the throwaway registry
    os.environ['REQUESTS_CA_BUNDLE'] = ca_cert_path
    return oc.Client(
        credentials_lookup=lambda **kwargs: None,
        routes=oc.OciRoutes(),
    )


def test_blob_roundtrip_same_host_is_not_blocked(tls_registry):
    host, ca = tls_registry
    client = _client(ca)

    ref = f'{host}/roundtrip:1'
    blob = b'hello-cc-utils-integration'
    digest = 'sha256:' + hashlib.sha256(blob).hexdigest()

    # push exercises the real blob-upload flow, including the Location header the host guard checks
    client.put_blob(image_reference=ref, digest=digest, octets_count=len(blob), data=blob)

    # pull it back and confirm the bytes survive the round-trip
    pulled = client.blob(image_reference=ref, digest=digest).content
    assert pulled == blob


def test_blob_exists_check_same_host(tls_registry):
    host, ca = tls_registry
    client = _client(ca)

    ref = f'{host}/roundtrip:1'
    blob = b'exists-probe'
    digest = 'sha256:' + hashlib.sha256(blob).hexdigest()

    client.put_blob(image_reference=ref, digest=digest, octets_count=len(blob), data=blob)
    # head_blob / blob-exists goes through _request too; must not be blocked for same-host
    assert client.blob(image_reference=ref, digest=digest, absent_ok=True) is not None
