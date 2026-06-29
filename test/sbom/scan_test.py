#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''Unit tests for scan.py (_resolve_single_arch_ref).'''
import importlib.util
import os
import sys
import types
import unittest.mock as mock

import pytest

# ---------------------------------------------------------------------------
# Load scan.py as a module without executing main()
# ---------------------------------------------------------------------------
_scan_py = os.path.join(
    os.path.dirname(__file__),
    '../../.github/actions/sbom-upload/scan.py',
)


def _load_scan():
    # ensure the project root is first so the local sbom/ package wins over any
    # stale pip-installed version (same trick scan.py itself uses at runtime)
    _root = os.path.dirname(os.path.dirname(os.path.dirname(_scan_py)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    # invalidate any already-cached stale sbom.inject before loading scan.py
    for key in list(sys.modules):
        if key == 'sbom' or key.startswith('sbom.'):
            del sys.modules[key]

    spec = importlib.util.spec_from_file_location('scan', _scan_py)
    mod = importlib.util.module_from_spec(spec)
    for name in ('cnudie', 'cnudie.retrieve', 'oci.auth', 'ocm', 'ocm.iter'):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    spec.loader.exec_module(mod)
    return mod


_scan = _load_scan()
_resolve = _scan._resolve_single_arch_ref


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_platform(os_name, arch):
    p = mock.MagicMock()
    p.os = os_name
    p.architecture = arch
    return p


def _make_entry(digest, os_name='linux', arch='amd64'):
    e = mock.MagicMock()
    e.digest = digest
    e.platform = _make_platform(os_name, arch)
    return e


def _manifest_list(*entries):
    import oci.model as om
    ml = mock.MagicMock(spec=om.OciImageManifestList)
    ml.manifests = list(entries)
    return ml


def _single_manifest(layers=None):
    import oci.model as om
    m = mock.MagicMock(spec=om.OciImageManifest)
    if layers is not None:
        m.layers = layers
    return m


def _make_layer(size):
    layer = mock.MagicMock()
    layer.size = size
    return layer


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_single_arch_image_returns_digest_ref():
    manifest_bytes = b'{"schemaVersion":2}'
    layers = [_make_layer(100), _make_layer(200)]
    client = mock.MagicMock()
    client.manifest.return_value = _single_manifest(layers=layers)
    client.manifest_raw.return_value = mock.MagicMock(content=manifest_bytes)

    digest_ref, layer_count, compressed_bytes = _resolve('registry.example.com/repo:tag', client)

    import hashlib
    expected_digest = 'sha256:' + hashlib.sha256(manifest_bytes).hexdigest()
    assert digest_ref == f'registry.example.com/repo@{expected_digest}'
    assert layer_count == 2
    assert compressed_bytes == 300


def test_manifest_list_prefers_linux_amd64():
    layers = [_make_layer(512)]
    client = mock.MagicMock()
    arm = _make_entry('sha256:arm', 'linux', 'arm64')
    amd = _make_entry('sha256:amd64', 'linux', 'amd64')
    client.manifest.side_effect = [
        _manifest_list(arm, amd),       # first call: manifest list
        _single_manifest(layers=layers), # second call: resolved single-arch
    ]

    digest_ref, layer_count, compressed_bytes = _resolve('registry.example.com/repo:tag', client)

    assert digest_ref == 'registry.example.com/repo@sha256:amd64'
    assert layer_count == 1
    assert compressed_bytes == 512


def test_manifest_list_falls_back_to_first_when_no_amd64():
    layers = [_make_layer(1024)]
    client = mock.MagicMock()
    arm = _make_entry('sha256:arm', 'linux', 'arm64')
    win = _make_entry('sha256:win', 'windows', 'amd64')
    client.manifest.side_effect = [
        _manifest_list(arm, win),
        _single_manifest(layers=layers),
    ]

    digest_ref, layer_count, compressed_bytes = _resolve('registry.example.com/repo:tag', client)

    assert digest_ref == 'registry.example.com/repo@sha256:arm'
    assert layer_count == 1
    assert compressed_bytes == 1024


def test_manifest_list_empty_returns_none():
    client = mock.MagicMock()
    ml = mock.MagicMock()
    import oci.model as om
    ml.__class__ = om.OciImageManifestList
    ml.manifests = []
    client.manifest.return_value = ml

    digest_ref, layer_count, compressed_bytes = _resolve('registry.example.com/repo:tag', client)

    assert digest_ref is None
    assert layer_count is None
    assert compressed_bytes is None


def test_client_error_returns_none(capsys):
    client = mock.MagicMock()
    client.manifest.side_effect = Exception('connection refused')

    digest_ref, layer_count, compressed_bytes = _resolve('registry.example.com/repo:tag', client)

    assert digest_ref is None
    assert layer_count is None
    assert compressed_bytes is None
    assert 'warning' in capsys.readouterr().err.lower()


def test_digest_addressed_single_arch_preserves_repo():
    '''Digest refs that are already single-arch should still return repo@digest form.'''
    import hashlib
    manifest_bytes = b'{"schemaVersion":2,"layers":[]}'
    layers = [_make_layer(64), _make_layer(128)]
    client = mock.MagicMock()
    client.manifest.return_value = _single_manifest(layers=layers)
    client.manifest_raw.return_value = mock.MagicMock(content=manifest_bytes)

    ref = 'registry.example.com/foo/bar@sha256:' + 'a' * 64
    digest_ref, layer_count, compressed_bytes = _resolve(ref, client)

    expected = 'sha256:' + hashlib.sha256(manifest_bytes).hexdigest()
    assert digest_ref is not None
    assert digest_ref.startswith('registry.example.com/foo/bar@')
    assert digest_ref.endswith(expected)
    assert layer_count == 2
    assert compressed_bytes == 192
