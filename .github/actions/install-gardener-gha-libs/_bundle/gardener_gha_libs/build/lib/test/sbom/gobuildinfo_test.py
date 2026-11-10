# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0
import io
import json
import os
import shutil
import subprocess
import tarfile

import pytest

import sbom.gobinary as sgob
import sbom.gobuildinfo as sgbi
import sbom.inject as sinj

_INFO_START = bytes.fromhex('3077af0c9274080241e1c107e6d618e6')
_INFO_END = bytes.fromhex('f932433186182072008242104116d8f2')


def _uvarint(n):
    out = b''
    while True:
        b = n & 0x7f
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _blob(version, modinfo, flags=0x2, offset=0):
    framed = _INFO_START + modinfo.encode() + _INFO_END
    header = b'\xff Go buildinf:' + bytes([8, flags]) + b'\0' * 16
    body = _uvarint(len(version)) + version.encode() + _uvarint(len(framed)) + framed
    return b'\0' * offset + header + body + b'\0' * 64


_MODINFO = (
    'path\texample.com/cmd/x\n'
    'mod\texample.com\tv1.2.3\th1:abc=\n'
    'dep\tgolang.org/x/crypto\tv0.30.0\th1:def=\n'
    '=>\t../crypto\t\n'
    'build\t-tags=foo,bar\n'
    'build\tCGO_ENABLED=0\n'
    'build\tDefaultGODEBUG=tlssha1=1,x509negativeserial=1\n'
)


def test_parse_inline():
    info = sgbi.parse_buildinfo(io.BytesIO(_blob('go1.23.4', _MODINFO, offset=4096)))
    assert info['go_version'] == 'go1.23.4'
    assert info['path'] == 'example.com/cmd/x'
    assert info['main_module'] == {'path': 'example.com', 'version': 'v1.2.3'}
    assert info['deps'] == [{
        'path': 'golang.org/x/crypto',
        'version': 'v0.30.0',
        'replace': {'path': '../crypto', 'version': ''},
    }]
    assert info['settings'] == {
        '-tags': 'foo,bar',
        'CGO_ENABLED': '0',
        'DefaultGODEBUG': 'tlssha1=1,x509negativeserial=1',
    }


def test_unaligned_magic_ignored():
    assert sgbi.parse_buildinfo(io.BytesIO(_blob('go1.23.4', _MODINFO, offset=5))) is None


def test_magic_across_chunk_boundary(monkeypatch):
    # aligned offset 4080; magic straddles the 4090-byte read boundary
    monkeypatch.setattr(sgbi, '_CHUNK', 4090)
    data = _blob('go1.22.0', _MODINFO, offset=4080)
    info = sgbi.parse_buildinfo(io.BytesIO(data))
    assert info['go_version'] == 'go1.22.0'


def test_pre_118_pointer_format():
    assert sgbi.parse_buildinfo(io.BytesIO(_blob('', '', flags=0x0))) == {
        'go_version': None,
        'godebug_literals': {},
        'godebug_mentions': {},
    }


def test_godebug_literals_beyond_default(monkeypatch):
    # one copy as part of DefaultGODEBUG (here: the build-info blob), one standalone;
    # small chunks so hits straddle read boundaries
    monkeypatch.setattr(sgbi, '_CHUNK', 100)
    prefix = (
        b'\0' * 37 + b'GODEBUG\0tlssha1=1\0' + b'\0' * 211 + b'xx,fips140=on,yy'
        + b'(temporarily override with GODEBUG=x509sha1=1)'
    )
    prefix += b'\0' * (-len(prefix) % 16)  # build-info magic is 16-byte aligned
    data = prefix + _blob('go1.26.0', _MODINFO)
    info = sgbi.parse_buildinfo(io.BytesIO(data))
    assert info['godebug_literals'] == {'tlssha1=1': 1, 'fips140=on': 1}
    assert info['godebug_mentions'] == {'x509sha1=1': 1}


def test_godebug_literal_only_in_default():
    info = sgbi.parse_buildinfo(io.BytesIO(_blob('go1.26.0', _MODINFO)))
    assert info['godebug_literals'] == {}


def test_non_go():
    assert sgbi.parse_buildinfo(io.BytesIO(b'\x7fELF' + b'\0' * 10000)) is None


def test_scan_binaries_skips_non_go_and_sorts():
    entries = [
        ('/z', io.BytesIO(_blob('go1.25.0', _MODINFO))),
        ('/sh', io.BytesIO(b'\x7fELF' + b'\0' * 100)),
        ('/a', io.BytesIO(_blob('go1.24.0', _MODINFO))),
    ]
    assert [b['binary'] for b in sgbi.scan_binaries(entries)] == ['/a', '/z']


def _go_build(tmp_path, go_directive, main_body):
    (tmp_path / 'go.mod').write_text(f'module example.com/t\n\ngo {go_directive}\n')
    (tmp_path / 'main.go').write_text(
        f'package main\nimport ("fmt"; "os")\nfunc main() {{ {main_body}; '
        'fmt.Println(os.Args) }\n'
    )
    binary = str(tmp_path / 't')
    subprocess.run(
        ['go', 'build', '-ldflags=-s -w', '-o', binary, '.'],
        cwd=tmp_path,
        check=True,
        env={**os.environ, 'GOFLAGS': ''},
    )
    with open(binary, 'rb') as f:
        return sgbi.parse_buildinfo(f)


@pytest.mark.skipif(not shutil.which('go'), reason='go toolchain not available')
@pytest.mark.parametrize('go_directive', ('1.20', '1.26'))
def test_setenv_literal_detected(tmp_path, go_directive):
    plain = _go_build(tmp_path, go_directive, '_ = 0')
    assert plain['godebug_literals'] == {}
    setenv = _go_build(tmp_path, go_directive, 'os.Setenv("GODEBUG", "tlssha1=1")')
    assert setenv['godebug_literals'] == {'tlssha1=1': 1}


@pytest.mark.skipif(not shutil.which('go'), reason='go toolchain not available')
def test_matches_go_version_m(tmp_path):
    (tmp_path / 'go.mod').write_text('module example.com/t\n\ngo 1.20\n')
    (tmp_path / 'main.go').write_text(
        'package main\nimport ("crypto/sha1"; "fmt")\n'
        'func main() { fmt.Println(sha1.Sum(nil)) }\n'
    )
    binary = str(tmp_path / 't')
    subprocess.run(
        ['go', 'build', '-ldflags=-s -w', '-tags', 'foo', '-o', binary, '.'],
        cwd=tmp_path,
        check=True,
        env={**os.environ, 'GOOS': 'linux', 'GOARCH': 'arm64', 'GOFLAGS': ''},
    )
    want = {}
    out = subprocess.run(['go', 'version', '-m', binary], capture_output=True, text=True)
    lines = out.stdout.splitlines()
    for line in lines[1:]:
        fields = line.strip().split('\t')
        if fields[0] == 'build':
            key, _, value = fields[1].partition('=')
            want[key] = value
    with open(binary, 'rb') as f:
        info = sgbi.parse_buildinfo(f)
    assert info['go_version'] == lines[0].split(': ', 1)[1]
    assert info['path'] == 'example.com/t'
    assert info['settings'] == want


def _layer(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        for name, data, mode in files:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_iter_executables_overlay(monkeypatch):
    elf_go = b'\x7fELF' + b'\0' * 12 + _blob('go1.24.1', _MODINFO)
    lower = _layer([
        ('bin/old', elf_go, 0o755),
        ('bin/gone', elf_go, 0o755),
        ('etc/conf', b'x', 0o644),
        ('bin/script', b'#!/bin/sh\n', 0o755),
    ])
    upper = _layer([
        ('bin/old', b'\x7fELF' + b'\0' * 100, 0o755),
        ('bin/.wh.gone', b'', 0o644),
        ('manager', elf_go, 0o755),
        ('entrypoint.sh', b'#!/bin/sh\nexport GODEBUG=tlssha1=1\nexec /manager\n', 0o755),
    ])

    class _Layer:
        def __init__(self, digest):
            self.digest = digest

    class _Manifest:
        layers = [_Layer('lower'), _Layer('upper')]

    class _Resp:
        def __init__(self, data):
            self.raw = io.BytesIO(data)

        def close(self):
            pass

    class _Client:
        def manifest(self, image_reference):
            return _Manifest()

        def blob(self, image_reference, digest, stream):
            return _Resp({'lower': lower, 'upper': upper}[digest])

    binaries = sgbi.scan_binaries(
        sinj._iter_executables('example.com/repo:tag', _Client()),
    )
    assert [b['binary'] for b in binaries] == ['/manager']

    binaries, assignments = sinj.scan_go_settings('example.com/repo:tag', _Client())
    assert [b['binary'] for b in binaries] == ['/manager']
    assert assignments == [
        {'path': '/entrypoint.sh', 'lineno': 2, 'line': 'export GODEBUG=tlssha1=1'},
    ]


@pytest.mark.parametrize('line, matches', (
    ('export GODEBUG=tlssha1=1', True),
    ('GODEBUG=x509sha1=1 exec /manager "$@"', True),
    ('  GODEBUG: "tlssha1=1"', True),
    ('{"GODEBUG": "tlssha1=1"}', True),
    ('Environment=GODEBUG=tlssha1=1', True),
    ('echo $GODEBUG', False),
    ('v := os.Getenv("GODEBUG")', False),
    ('MYGODEBUG=1', False),
))
def test_scan_text_files(line, matches):
    found = sgbi.scan_text_files([('/f', io.BytesIO(b'#!/bin/sh\n' + line.encode() + b'\n'))])
    assert found == ([{'path': '/f', 'lineno': 2, 'line': line.strip()}] if matches else [])


def test_scan_text_files_skips_binary_and_large(monkeypatch):
    monkeypatch.setattr(sgbi, '_TEXT_MAX_SIZE', 64)
    assert sgbi.scan_text_files([
        ('/bin', io.BytesIO(b'GODEBUG=tlssha1=1\0')),
        ('/big', io.BytesIO(b'GODEBUG=tlssha1=1\n' + b'x' * 64)),
    ]) == []


def test_modules_from_cdx_location_properties():
    doc = {'components': [
        {
            'purl': 'pkg:golang/golang.org/x/crypto@v0.1.0',
            'properties': [{'name': 'syft:location:0:path', 'value': '/a'}],
        },
        {
            'purl': 'pkg:golang/golang.org/x/crypto@v0.2.0',
            'properties': [{'name': 'syft:location:0:path', 'value': '/b'}],
        },
        {
            'purl': 'pkg:golang/stdlib@1.24.0?package-id=1',
            'evidence': {'occurrences': [{'location': '/a'}]},
        },
    ]}
    assert sgob._modules_from_cdx(doc) == {
        'golang.org/x/crypto': ['/a', '/b'],
        'stdlib': ['/a'],
    }


def test_build_document_roundtrip():
    doc = json.loads(sgbi.build_document('example.com/r@sha256:00', [{'binary': '/x'}]))
    assert doc['schemaVersion'] == 1
    assert doc['binaries'] == [{'binary': '/x'}]
