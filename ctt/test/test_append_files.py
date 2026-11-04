# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import hashlib
import io
import json
import tarfile

import pytest

import ctt.model
import ctt.oci_util
import oci.model as om


DOCKER_MANIFEST_V2 = 'application/vnd.docker.distribution.manifest.v2+json'


def _mk_tar_gz_blob(entries: list[tuple[str, bytes]]) -> tuple[bytes, str, str]:
    '''
    Returns (gzipped-blob-bytes, gzipped-digest, non-gzipped-digest).
    `entries` is a list of (name, contents) for regular files.
    '''
    import gzip

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode='w') as tf:
        for name, contents in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(contents)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(contents))
    tar_bytes = tar_buf.getvalue()
    gz_bytes = gzip.compress(tar_bytes)
    return (
        gz_bytes,
        'sha256:' + hashlib.sha256(gz_bytes).hexdigest(),
        'sha256:' + hashlib.sha256(tar_bytes).hexdigest(),
    )


def _mk_cfg_blob(diff_ids: list[str]) -> tuple[bytes, str]:
    cfg = {
        'architecture': 'amd64',
        'os': 'linux',
        'rootfs': {
            'type': 'layers',
            'diff_ids': diff_ids,
        },
    }
    cfg_bytes = json.dumps(cfg).encode('utf-8')
    return cfg_bytes, 'sha256:' + hashlib.sha256(cfg_bytes).hexdigest()


def _mk_manifest(layers: list[tuple[str, str]], cfg_digest: str) -> tuple[bytes, str]:
    '''
    `layers`: list of (gzipped-digest, media-type).
    Returns (manifest-bytes, manifest-digest-with-alg).
    '''
    manifest = {
        'schemaVersion': 2,
        'mediaType': DOCKER_MANIFEST_V2,
        'config': {
            'mediaType': 'application/vnd.docker.container.image.v1+json',
            'digest': cfg_digest,
            'size': 1,
        },
        'layers': [
            {
                'mediaType': mt,
                'digest': digest,
                'size': 1,
            } for digest, mt in layers
        ],
    }
    manifest_bytes = json.dumps(manifest).encode('utf-8')
    return manifest_bytes, 'sha256:' + hashlib.sha256(manifest_bytes).hexdigest()


class FakeOciClient:
    '''
    In-memory fake for ctt.oci_util.filter_image tests. Not a full implementation of the
    oci.client.Client interface, but covers the operations filter_image exercises.
    '''

    def __init__(self, manifests, blobs):
        self._manifests = dict(manifests)   # ref -> (raw-bytes, content-type)
        self._blobs = dict(blobs)           # digest -> bytes
        self.pushed_manifests = {}          # ref -> bytes (as passed)
        self.pushed_blobs = {}              # digest -> bytes

    def manifest(self, image_reference, absent_ok=False, accept=None):
        raw, _content_type = self._manifests[str(image_reference)]
        return self._manifest_dict_to_model(raw)

    def _manifest_dict_to_model(self, raw):
        if isinstance(raw, (bytes, bytearray)):
            raw = json.loads(raw)
        cfg = raw['config']
        layers = raw['layers']
        return om.OciImageManifest(
            config=om.OciBlobRef(**cfg),
            layers=[om.OciBlobRef(**l) for l in layers],
            mediaType=raw.get('mediaType', DOCKER_MANIFEST_V2),
        )

    def head_blob(self, image_reference, digest, absent_ok=True):
        return False  # pretend absent

    def blob(self, image_reference, digest, stream=False, **kwargs):
        content = self._blobs[digest]

        class Response:
            pass
        response = Response()
        response.content = content
        response.iter_content = lambda chunk_size: iter([content])
        return response

    def put_blob(self, image_reference, digest, octets_count, data):
        if hasattr(data, 'read'):
            data = data.read()
        elif hasattr(data, 'content'):
            data = data.content
        elif hasattr(data, 'iter_content'):
            data = b''.join(data.iter_content(chunk_size=64 * 1024))
        elif not isinstance(data, (bytes, bytearray)):
            # generator of chunks
            data = b''.join(data)
        self.pushed_blobs[digest] = bytes(data)

    def put_manifest(self, image_reference, manifest):
        self.pushed_manifests[str(image_reference)] = manifest

        class R:
            def raise_for_status(self):
                return None
        return R()


def _gz_decompress(blob_bytes):
    import gzip
    return gzip.decompress(blob_bytes)


def _tar_members(tar_bytes) -> dict[str, bytes]:
    res = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:*') as tf:
        for m in tf.getmembers():
            if m.isfile():
                res[m.name] = tf.extractfile(m).read()
            elif m.isdir():
                res[m.name] = None
    return res


@pytest.fixture
def src_layout(tmp_path):
    '''
    Builds a fake in-memory image with two layers:
    - layer 1: /etc/ssl/certs/ca-certificates.crt   ('vendor-content')
    - layer 2: /etc/os-release                      ('os-info')
    Returns (manifest_bytes, manifest_ref, blobs).
    '''
    layer1_gz, layer1_gz_digest, layer1_ungz_digest = _mk_tar_gz_blob([
        ('etc/ssl/certs/ca-certificates.crt', b'vendor-content'),
    ])
    layer2_gz, layer2_gz_digest, layer2_ungz_digest = _mk_tar_gz_blob([
        ('etc/os-release', b'os-info'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer1_ungz_digest, layer2_ungz_digest])

    manifest_bytes, _manifest_digest = _mk_manifest(
        layers=[
            (layer1_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip'),
            (layer2_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip'),
        ],
        cfg_digest=cfg_digest,
    )

    blobs = {
        layer1_gz_digest: layer1_gz,
        layer2_gz_digest: layer2_gz,
        cfg_digest: cfg_bytes,
    }
    return manifest_bytes, blobs, layer1_ungz_digest, layer2_ungz_digest


def _write_payload(tmp_path, name, contents: bytes):
    p = tmp_path / name
    p.write_bytes(contents)
    return str(p)


def test_append_files_new_layer_mode(tmp_path, src_layout):
    manifest_bytes, blobs, _, _ = src_layout
    tgt_path = '/etc/ssl/certs/ca-certificates.crt'
    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    res, tgt_ref, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        append_files=[ctt.model.AppendFilesEntry(src_path=payload, tgt_path=tgt_path)],
        append_mode=ctt.model.AppendMode.NEW_LAYER,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    assert len(pushed_manifest['layers']) == 3, (
        f'expected 3 layers after NEW_LAYER append, got {len(pushed_manifest["layers"])}'
    )

    # new layer is the last one; fetch it from pushed_blobs and check contents
    new_layer_digest = pushed_manifest['layers'][-1]['digest']
    new_layer_tar = _gz_decompress(client.pushed_blobs[new_layer_digest])
    members = _tar_members(new_layer_tar)
    assert 'etc/ssl/certs/ca-certificates.crt' in members
    assert members['etc/ssl/certs/ca-certificates.crt'] == b'curated-cert'

    # cfg blob diff_ids must have been extended by exactly one (the appended layer)
    new_cfg_digest = pushed_manifest['config']['digest']
    new_cfg = json.loads(client.pushed_blobs[new_cfg_digest])
    assert len(new_cfg['rootfs']['diff_ids']) == 3


def test_append_files_replace_removed_mode(tmp_path, src_layout):
    manifest_bytes, blobs, _, _ = src_layout
    tgt_path = '/etc/ssl/certs/ca-certificates.crt'
    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    res, tgt_ref, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_files=['etc/ssl/certs/ca-certificates.crt'],
        append_files=[ctt.model.AppendFilesEntry(src_path=payload, tgt_path=tgt_path)],
        append_mode=ctt.model.AppendMode.REPLACE_REMOVED,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    # replacement is in-place: still 2 layers
    assert len(pushed_manifest['layers']) == 2

    # layer 1 (the one that contained the removed file) must now contain curated-cert
    # and layer 1's digest must have changed
    new_layer1_digest = pushed_manifest['layers'][0]['digest']
    new_layer1_tar = _gz_decompress(client.pushed_blobs[new_layer1_digest])
    members = _tar_members(new_layer1_tar)
    assert members.get('etc/ssl/certs/ca-certificates.crt') == b'curated-cert'


def test_append_files_replace_removed_fallback_to_new_layer(tmp_path, src_layout):
    '''
    When remove_files matches nothing in any layer, REPLACE_REMOVED must fall back
    to appending a new layer.
    '''
    manifest_bytes, blobs, _, _ = src_layout
    tgt_path = '/opt/bundle.pem'
    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    res, tgt_ref, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_files=['no/such/file'],
        append_files=[ctt.model.AppendFilesEntry(src_path=payload, tgt_path=tgt_path)],
        append_mode=ctt.model.AppendMode.REPLACE_REMOVED,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    assert len(pushed_manifest['layers']) == 3, 'must fall back to NEW_LAYER when no layer matched'
    new_layer_digest = pushed_manifest['layers'][-1]['digest']
    members = _tar_members(_gz_decompress(client.pushed_blobs[new_layer_digest]))
    # dir entry is added without trailing slash; check file presence
    assert 'opt/bundle.pem' in members
    assert members['opt/bundle.pem'] == b'curated-cert'


def test_append_files_insert_into_layer(tmp_path, src_layout):
    manifest_bytes, blobs, _, _ = src_layout
    tgt_path = '/etc/ssl/certs/ca-certificates.crt'
    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    res, tgt_ref, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        append_files=[ctt.model.AppendFilesEntry(src_path=payload, tgt_path=tgt_path)],
        append_mode=ctt.model.AppendMode.INSERT_INTO_LAYER,
        append_layer_idx=-1,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    # in-place: still 2 layers
    assert len(pushed_manifest['layers']) == 2

    # last layer (idx=-1) must contain the new entry
    last_layer_digest = pushed_manifest['layers'][-1]['digest']
    members = _tar_members(_gz_decompress(client.pushed_blobs[last_layer_digest]))
    assert members.get('etc/ssl/certs/ca-certificates.crt') == b'curated-cert'


def test_append_files_insert_into_layer_out_of_range(tmp_path, src_layout):
    manifest_bytes, blobs, _, _ = src_layout
    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    with pytest.raises(ValueError, match='out of range'):
        ctt.oci_util.filter_image(
            source_ref=src_ref,
            target_ref='tgt.example.com/my/image:1',
            append_files=[ctt.model.AppendFilesEntry(src_path=payload, tgt_path='/x')],
            append_mode=ctt.model.AppendMode.INSERT_INTO_LAYER,
            append_layer_idx=99,
            oci_client=client,
        )


def test_append_files_strip_manifest_attributes_combination_rejected(tmp_path, src_layout):
    manifest_bytes, blobs, _, _ = src_layout
    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    with pytest.raises(NotImplementedError):
        ctt.oci_util.filter_image(
            source_ref=src_ref,
            target_ref='tgt.example.com/my/image:1',
            append_files=[ctt.model.AppendFilesEntry(src_path=payload, tgt_path='/x')],
            append_mode=ctt.model.AppendMode.NEW_LAYER,
            strip_manifest_attributes=['features'],
            oci_client=client,
        )


def test_append_files_directory_src(tmp_path):
    '''
    Directory src_path is replicated below tgt_path.
    '''
    layer_gz, layer_gz_digest, layer_ungz_digest = _mk_tar_gz_blob([
        ('etc/os-release', b'os-info'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer_ungz_digest])
    manifest_bytes, _manifest_digest = _mk_manifest(
        layers=[(layer_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip')],
        cfg_digest=cfg_digest,
    )

    src_dir = tmp_path / 'bundle'
    src_dir.mkdir()
    (src_dir / 'a.pem').write_bytes(b'aaa')
    (src_dir / 'sub').mkdir()
    (src_dir / 'sub' / 'b.pem').write_bytes(b'bbb')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs={layer_gz_digest: layer_gz, cfg_digest: cfg_bytes},
    )

    res, tgt_ref, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        append_files=[ctt.model.AppendFilesEntry(src_path=str(src_dir), tgt_path='/etc/ssl/certs')],
        append_mode=ctt.model.AppendMode.NEW_LAYER,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    new_layer_digest = pushed_manifest['layers'][-1]['digest']
    members = _tar_members(_gz_decompress(client.pushed_blobs[new_layer_digest]))
    assert members.get('etc/ssl/certs/a.pem') == b'aaa'
    assert members.get('etc/ssl/certs/sub/b.pem') == b'bbb'


def test_append_files_idempotent(tmp_path, src_layout):
    '''
    Two runs on the same input must produce identical digests (deterministic tar entries:
    uid=gid=0, mtime=0).
    '''
    manifest_bytes, blobs, _, _ = src_layout
    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    def _run():
        client = FakeOciClient(
            manifests={
                'src.example.com/my/image:1': (json.loads(manifest_bytes), DOCKER_MANIFEST_V2),
            },
            blobs=dict(blobs),
        )
        _, tgt_ref, manifest_raw = ctt.oci_util.filter_image(
            source_ref='src.example.com/my/image:1',
            target_ref='tgt.example.com/my/image:1',
            append_files=[ctt.model.AppendFilesEntry(src_path=payload, tgt_path='/x')],
            append_mode=ctt.model.AppendMode.NEW_LAYER,
            oci_client=client,
        )
        return tgt_ref, manifest_raw

    tgt_ref_a, manifest_raw_a = _run()
    tgt_ref_b, manifest_raw_b = _run()
    assert tgt_ref_a == tgt_ref_b
    assert manifest_raw_a == manifest_raw_b
