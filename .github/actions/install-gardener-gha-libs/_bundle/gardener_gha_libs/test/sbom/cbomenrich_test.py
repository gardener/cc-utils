#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''Unit tests for error-propagation in cbomenrich reader functions.'''
import gzip
import io
import sys
import os
import tarfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import sbom.cbomenrich as scbe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tar(filename, content):
    '''Return bytes of a tar archive containing one file.'''
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_gzip_tar(filename, content):
    return gzip.compress(_make_tar(filename, content))


# ---------------------------------------------------------------------------
# _docker_file_reader
# ---------------------------------------------------------------------------

class _FakeCompletedProcess:
    def __init__(self, returncode, stdout=b''):
        self.returncode = returncode
        self.stdout = stdout


def test_docker_read_file_not_found(monkeypatch):
    '''returncode != 0 → None (not-found path preserved).'''
    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(returncode=1)

    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/docker')
    monkeypatch.setattr(
        'subprocess.check_output',
        lambda *a, **kw: 'fake-container-id\n',
    )
    monkeypatch.setattr('subprocess.run', fake_run)

    reader, cleanup = scbe._docker_file_reader('example.com/img:tag')
    assert reader('/etc/passwd') is None
    cleanup()


def test_docker_read_file_corrupt_tar_raises(monkeypatch):
    '''Corrupt tar data must raise, not return None.'''
    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(returncode=0, stdout=b'this is not a tar archive')

    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/docker')
    monkeypatch.setattr(
        'subprocess.check_output',
        lambda *a, **kw: 'fake-container-id\n',
    )
    monkeypatch.setattr('subprocess.run', fake_run)

    reader, cleanup = scbe._docker_file_reader('example.com/img:tag')
    with pytest.raises(Exception):
        reader('/etc/passwd')
    cleanup()


# ---------------------------------------------------------------------------
# _oci_file_reader  (lightweight mock OCI client — no unittest.mock)
# ---------------------------------------------------------------------------

class _FakeManifest:
    def __init__(self, layers):
        self.layers = layers


class _FakeLayer:
    def __init__(self, digest, data):
        self.digest = digest
        self._data = data

    mediaType = 'application/vnd.oci.image.layer.v1.tar+gzip'


class _FakeResponse:
    def __init__(self, content):
        self.content = content

    def iter_content(self, chunk_size=65536):
        yield self.content


class _OciClientManifestFails:
    def manifest(self, *a, **kw):
        raise RuntimeError('network error')

    def blob(self, *a, **kw):
        raise AssertionError('should not be reached')


class _OciClientBlobFails:
    def __init__(self, manifest):
        self._manifest = manifest

    def manifest(self, *a, **kw):
        return self._manifest

    def blob(self, *a, **kw):
        raise RuntimeError('blob download failed')


class _OciClientCorruptLayer:
    def __init__(self, manifest):
        self._manifest = manifest

    def manifest(self, *a, **kw):
        return self._manifest

    def blob(self, *a, **kw):
        return _FakeResponse(b'not valid gzip or tar data')


class _OciClientGoodLayer:
    def __init__(self, manifest, layer_data):
        self._manifest = manifest
        self._layer_data = layer_data

    def manifest(self, *a, **kw):
        return self._manifest

    def blob(self, *a, **kw):
        return _FakeResponse(self._layer_data)


def _patch_oci_model(monkeypatch, manifest):
    '''Patch oci.model so _oci_file_reader can import it.'''
    import types

    fake_ref = types.SimpleNamespace(ref_without_tag='example.com/repo')
    fake_om = types.ModuleType('oci.model')
    fake_om.OciImageReference = types.SimpleNamespace(
        to_image_ref=lambda _ref: fake_ref,
    )
    fake_om.OciImageManifestList = type('OciImageManifestList', (), {})

    import sys
    # Ensure oci package exists
    if 'oci' not in sys.modules:
        oci_pkg = types.ModuleType('oci')
        sys.modules['oci'] = oci_pkg
    sys.modules['oci.model'] = fake_om
    return fake_om


def test_oci_manifest_fetch_raises(monkeypatch):
    '''manifest() throwing must propagate out of _oci_file_reader.'''
    _patch_oci_model(monkeypatch, None)
    client = _OciClientManifestFails()
    with pytest.raises(RuntimeError, match='network error'):
        scbe._oci_file_reader('example.com/img:tag', client)


def test_oci_load_layer_raises(monkeypatch):
    '''blob() throwing must propagate when read_file is called.'''
    layer = _FakeLayer('sha256:abc123', b'')
    manifest = _FakeManifest([layer])
    _patch_oci_model(monkeypatch, manifest)
    client = _OciClientBlobFails(manifest)
    reader = scbe._oci_file_reader('example.com/img:tag', client)
    assert reader is not None
    with pytest.raises(RuntimeError, match='blob download failed'):
        reader('/etc/passwd')


def test_oci_read_file_corrupt_tar_raises(monkeypatch):
    '''Corrupt layer data (not valid tar) must raise.'''
    layer = _FakeLayer('sha256:def456', b'')
    manifest = _FakeManifest([layer])
    _patch_oci_model(monkeypatch, manifest)
    # Return raw (non-gzip) bytes that are not a valid tar
    client = _OciClientCorruptLayer(manifest)
    reader = scbe._oci_file_reader('example.com/img:tag', client)
    assert reader is not None
    with pytest.raises(Exception):
        reader('/etc/passwd')


def test_oci_read_file_not_found_returns_none(monkeypatch):
    '''File absent from all layers returns None (not-found path preserved).'''
    tar_data = _make_tar('./other/file.txt', b'hello')
    layer = _FakeLayer('sha256:fff000', tar_data)
    manifest = _FakeManifest([layer])
    _patch_oci_model(monkeypatch, manifest)
    client = _OciClientGoodLayer(manifest, tar_data)
    reader = scbe._oci_file_reader('example.com/img:tag', client)
    assert reader is not None
    assert reader('/etc/passwd') is None


def test_oci_read_file_found_returns_bytes(monkeypatch):
    '''File present in a layer is returned correctly.'''
    content = b'root:x:0:0:root:/root:/bin/sh\n'
    tar_data = _make_tar('./etc/passwd', content)
    layer = _FakeLayer('sha256:aaa111', tar_data)
    manifest = _FakeManifest([layer])
    _patch_oci_model(monkeypatch, manifest)
    client = _OciClientGoodLayer(manifest, tar_data)
    reader = scbe._oci_file_reader('example.com/img:tag', client)
    assert reader is not None
    assert reader('/etc/passwd') == content
