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


def test_filter_image_remove_file_globs(tmp_path):
    '''
    `remove_file_globs` must remove tar members whose paths match any of the glob patterns
    (fnmatch, after stripping leading `/` from both pattern and member name).
    '''
    layer_gz, layer_gz_digest, layer_ungz_digest = _mk_tar_gz_blob([
        ('etc/ssl/certs/ca-certificates.crt', b'bundle'),
        ('etc/ssl/certs/00673b5b.0', b'hash-link-slug'),
        ('etc/ssl/certs/ACCVRAIZ1.pem', b'per-ca-pem'),
        ('etc/ssl/certs/another-dir/file.txt', b'other'),
        ('etc/os-release', b'os-info'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer_ungz_digest])
    manifest_bytes, _manifest_digest = _mk_manifest(
        layers=[(layer_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip')],
        cfg_digest=cfg_digest,
    )
    blobs = {layer_gz_digest: layer_gz, cfg_digest: cfg_bytes}

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    _, _, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_file_globs=['/etc/ssl/certs/*.pem', 'etc/ssl/certs/*.0'],
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    new_layer_digest = pushed_manifest['layers'][0]['digest']
    new_layer_tar = _gz_decompress(client.pushed_blobs[new_layer_digest])
    members = _tar_members(new_layer_tar)

    assert 'etc/ssl/certs/ca-certificates.crt' in members
    assert 'etc/ssl/certs/00673b5b.0' not in members
    assert 'etc/ssl/certs/ACCVRAIZ1.pem' not in members
    assert 'etc/ssl/certs/another-dir/file.txt' in members
    assert 'etc/os-release' in members


def test_filter_image_globs_alone_skips_noop_shortcut(tmp_path, src_layout):
    '''
    When only `remove_file_globs` is set (no literal `remove_files`, no appends), the
    shortcut that returns `oci.replicate_artifact` unchanged must not be taken — the
    layer rewrite has to run so globs can be evaluated.
    '''
    layer_gz, layer_gz_digest, layer_ungz_digest = _mk_tar_gz_blob([
        ('etc/ssl/certs/junk.pem', b'remove-me'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer_ungz_digest])
    manifest_bytes, _ = _mk_manifest(
        layers=[(layer_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip')],
        cfg_digest=cfg_digest,
    )
    blobs = {layer_gz_digest: layer_gz, cfg_digest: cfg_bytes}

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    _, _, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_file_globs=['etc/ssl/certs/*.pem'],
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    new_layer_digest = pushed_manifest['layers'][0]['digest']
    members = _tar_members(_gz_decompress(client.pushed_blobs[new_layer_digest]))
    assert 'etc/ssl/certs/junk.pem' not in members


def test_filter_image_globs_and_replace_removed(tmp_path, src_layout):
    '''
    `replace_removed` append mode must trigger on layers that matched a glob, not just
    on layers that matched a literal `remove_files` entry.
    '''
    layer_gz, layer_gz_digest, layer_ungz_digest = _mk_tar_gz_blob([
        ('etc/ssl/certs/ca-certificates.crt', b'old-bundle'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer_ungz_digest])
    manifest_bytes, _ = _mk_manifest(
        layers=[(layer_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip')],
        cfg_digest=cfg_digest,
    )
    blobs = {layer_gz_digest: layer_gz, cfg_digest: cfg_bytes}

    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    _, _, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_file_globs=['etc/ssl/certs/*.crt'],
        append_files=[
            ctt.model.AppendFilesEntry(
                src_path=payload,
                tgt_path='/etc/ssl/certs/ca-certificates.crt',
            ),
        ],
        append_mode=ctt.model.AppendMode.REPLACE_REMOVED,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    # must be in-place replacement (1 layer), not NEW_LAYER fallback
    assert len(pushed_manifest['layers']) == 1
    new_layer_digest = pushed_manifest['layers'][0]['digest']
    members = _tar_members(_gz_decompress(client.pushed_blobs[new_layer_digest]))
    assert members.get('etc/ssl/certs/ca-certificates.crt') == b'curated-cert'


def test_filter_image_globs_combined_with_literal_remove_files(tmp_path, src_layout):
    '''Literal paths and globs are both honoured in the same filter pass.'''
    layer_gz, layer_gz_digest, layer_ungz_digest = _mk_tar_gz_blob([
        ('keep/me', b'k'),
        ('literal-remove', b'l'),
        ('glob-remove.pem', b'g'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer_ungz_digest])
    manifest_bytes, _ = _mk_manifest(
        layers=[(layer_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip')],
        cfg_digest=cfg_digest,
    )
    blobs = {layer_gz_digest: layer_gz, cfg_digest: cfg_bytes}

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    _, _, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_files=['literal-remove'],
        remove_file_globs=['*.pem'],
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    new_layer_digest = pushed_manifest['layers'][0]['digest']
    members = _tar_members(_gz_decompress(client.pushed_blobs[new_layer_digest]))
    assert 'keep/me' in members
    assert 'literal-remove' not in members
    assert 'glob-remove.pem' not in members


def test_filter_image_globs_do_not_prevent_new_layer_fallback(tmp_path):
    '''
    When globs are present but match nothing, and REPLACE_REMOVED AppendFiles was
    requested, the fallback to NEW_LAYER still applies.
    '''
    layer_gz, layer_gz_digest, layer_ungz_digest = _mk_tar_gz_blob([
        ('foo/bar', b'x'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer_ungz_digest])
    manifest_bytes, _ = _mk_manifest(
        layers=[(layer_gz_digest, 'application/vnd.docker.image.rootfs.diff.tar.gzip')],
        cfg_digest=cfg_digest,
    )
    blobs = {layer_gz_digest: layer_gz, cfg_digest: cfg_bytes}

    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    _, _, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_file_globs=['no/such/dir/*.pem'],
        append_files=[
            ctt.model.AppendFilesEntry(src_path=payload, tgt_path='/opt/bundle.pem'),
        ],
        append_mode=ctt.model.AppendMode.REPLACE_REMOVED,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    assert len(pushed_manifest['layers']) == 2, (
        'must fall back to NEW_LAYER when no glob matched any layer'
    )
    new_layer_digest = pushed_manifest['layers'][-1]['digest']
    members = _tar_members(_gz_decompress(client.pushed_blobs[new_layer_digest]))
    assert 'opt/bundle.pem' in members


def test_append_replace_removed_per_path_only_appends_where_removed(tmp_path):
    '''
    ReplaceRemoved must attach each append entry ONLY to layers where the
    corresponding tgt_path itself was removed — not to every layer that had ANY
    removal. Otherwise duplicate-within-a-layer situations occur when the base
    image already ships a file at tgt_path that did NOT match the remove set.
    '''
    # layer 1: contains ONLY the append-target (still present, not removed by any
    # filter). layer 2: contains an unrelated file that WILL match filter_files.
    # expected: append entry attached only to layer 2 (where something was
    # actually removed *that matches an AppendFiles tgt_path*). Since the only
    # removal in layer 2 is `junk` and NOT the append tgt, no append should
    # occur anywhere -> NEW_LAYER fallback fires.
    layer1_gz, layer1_gz_digest, layer1_ungz_digest = _mk_tar_gz_blob([
        ('etc/ssl1.1/cert.pem', b'original-cert'),
    ])
    layer2_gz, layer2_gz_digest, layer2_ungz_digest = _mk_tar_gz_blob([
        ('some/junk/file', b'junk'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer1_ungz_digest, layer2_ungz_digest])
    manifest_bytes, _ = _mk_manifest(
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

    payload = _write_payload(tmp_path, 'curated.pem', b'curated-cert')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    _, _, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_files=['some/junk/file'],
        append_files=[
            ctt.model.AppendFilesEntry(
                src_path=payload,
                tgt_path='/etc/ssl1.1/cert.pem',
            ),
        ],
        append_mode=ctt.model.AppendMode.REPLACE_REMOVED,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)
    # nothing matched in any layer for the append's tgt_path; NEW_LAYER fallback
    # kicks in with one extra layer carrying the bundle.
    assert len(pushed_manifest['layers']) == 3, (
        f'expected NEW_LAYER fallback to add one layer, got '
        f'{len(pushed_manifest["layers"])}'
    )
    # layer 1 (which had no removal at all) must still contain ONLY the original
    # file, exactly once
    new_layer1_digest = pushed_manifest['layers'][0]['digest']
    layer1_tar = _gz_decompress(client.pushed_blobs[new_layer1_digest])
    members = _tar_members(layer1_tar)
    if 'etc/ssl1.1/cert.pem' in members:
        # if it's present, it must NOT be the curated content (would be the dup bug)
        assert members['etc/ssl1.1/cert.pem'] != b'curated-cert', (
            'curated bundle leaked into a layer that never removed its tgt_path'
        )


def test_append_replace_removed_per_path_lands_only_in_matching_layer(tmp_path):
    '''
    When only ONE append tgt_path is removed in one layer (and other layers remove
    different paths), the append entry must land ONLY in the matching layer —
    never duplicated across layers that removed unrelated paths.
    '''
    # layer 1: removes ONLY 'etc/ssl/certs/ca-certificates.crt' (the append tgt).
    # layer 2: removes 'usr/share/grpc/roots.pem' (a different tgt of a different
    # append entry).
    layer1_gz, layer1_gz_digest, layer1_ungz_digest = _mk_tar_gz_blob([
        ('etc/ssl/certs/ca-certificates.crt', b'old-bundle'),
        ('etc/ssl/certs/junk.pem', b'junk'),
    ])
    layer2_gz, layer2_gz_digest, layer2_ungz_digest = _mk_tar_gz_blob([
        ('usr/share/grpc/roots.pem', b'old-grpc-roots'),
    ])
    cfg_bytes, cfg_digest = _mk_cfg_blob([layer1_ungz_digest, layer2_ungz_digest])
    manifest_bytes, _ = _mk_manifest(
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

    payload_cert = _write_payload(tmp_path, 'curated-cert.pem', b'curated-cert')
    payload_grpc = _write_payload(tmp_path, 'curated-grpc.pem', b'curated-grpc')

    src_ref = 'src.example.com/my/image:1'
    client = FakeOciClient(
        manifests={src_ref: (json.loads(manifest_bytes), DOCKER_MANIFEST_V2)},
        blobs=blobs,
    )

    _, _, manifest_raw = ctt.oci_util.filter_image(
        source_ref=src_ref,
        target_ref='tgt.example.com/my/image:1',
        remove_files=['etc/ssl/certs/ca-certificates.crt', 'usr/share/grpc/roots.pem'],
        remove_file_globs=['etc/ssl/certs/*.pem'],
        append_files=[
            ctt.model.AppendFilesEntry(
                src_path=payload_cert,
                tgt_path='/etc/ssl/certs/ca-certificates.crt',
            ),
            ctt.model.AppendFilesEntry(
                src_path=payload_grpc,
                tgt_path='/usr/share/grpc/roots.pem',
            ),
        ],
        append_mode=ctt.model.AppendMode.REPLACE_REMOVED,
        oci_client=client,
    )

    pushed_manifest = json.loads(manifest_raw)

    # per-layer expectations:
    # layer 1: removed `ca-certificates.crt` (literal) AND `junk.pem` (glob); append
    #          payload for `ca-certificates.crt` re-injected — but grpc payload
    #          must NOT land here
    # layer 2: removed `roots.pem`; append payload for `roots.pem` re-injected —
    #          but cert payload must NOT land here
    layer1_members = _tar_members(_gz_decompress(
        client.pushed_blobs[pushed_manifest['layers'][0]['digest']]
    ))
    assert layer1_members.get('etc/ssl/certs/ca-certificates.crt') == b'curated-cert'
    assert 'usr/share/grpc/roots.pem' not in layer1_members, (
        'grpc bundle must not be appended into the layer that removed only ca-certificates.crt'
    )
    assert 'etc/ssl/certs/junk.pem' not in layer1_members  # glob removed

    layer2_members = _tar_members(_gz_decompress(
        client.pushed_blobs[pushed_manifest['layers'][1]['digest']]
    ))
    assert layer2_members.get('usr/share/grpc/roots.pem') == b'curated-grpc'
    assert 'etc/ssl/certs/ca-certificates.crt' not in layer2_members, (
        'cert bundle must not be appended into the layer that removed only roots.pem'
    )

    # not a single layer must contain the same path twice
    for layer_digest in (l['digest'] for l in pushed_manifest['layers']):
        members = _tar_members(_gz_decompress(client.pushed_blobs[layer_digest]))
        names = list(members.keys())
        assert len(names) == len(set(names)), 'duplicate paths within layer'
