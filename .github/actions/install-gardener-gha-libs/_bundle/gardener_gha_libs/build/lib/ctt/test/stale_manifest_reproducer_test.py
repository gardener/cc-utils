# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import hashlib
import io
import json

import oci
import oci.model as om
import ocm.oci


class _HeadOkPutRecords:
    '''A minimal fake oci client that pretends a manifest exists (head ok) but the
    referenced blobs are gone (head 404). Models the "manifest present, blobs missing"
    state the gardener-installer v0.72.0 bug exposed, WITHOUT relying on any live
    registry or on Artifactory's race-window.
    '''

    def __init__(self):
        self.put_blobs = []
        self.put_manifests = []

    def put_blob(self, image_reference, digest, octets_count, data):
        self.put_blobs.append(digest)

    def head_blob(self, image_reference, digest):
        # blob is always absent
        class R:
            ok = False
        return R()

    def mount_blob(self, **kwargs):
        return False

    def blob(self, image_reference, digest):
        raise RuntimeError(f'unexpected fetch: {digest}')

    def head_manifest(self, image_reference, absent_ok=False, accept=None):
        # manifest present (this is where the ctt skip logic misfires)
        return om.OciBlobRef(digest='sha256:' + 'ab' * 32, mediaType='x', size=1)

    def put_manifest(self, image_reference, manifest):
        self.put_manifests.append(manifest)
        return manifest


def _mk_cd_tar(digest_marker: bytes):
    return component_descriptor_to_tar(const_bytes=digest_marker)


def component_descriptor_to_tar(const_bytes: bytes) -> io.BytesIO:
    '''Build a tiny in-memory tar stream (a single file) whose sha256 is deterministic.'''
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        data = const_bytes
        info = tarfile.TarInfo(name='component-descriptor.yaml')
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def test_overwrite_branch_does_not_shortcircuit_on_manifest_presence():
    '''Regression: even if a manifest already exists in the target (HEAD ok), the
    overwrite-path must still push the (re)generated blobs for it. It must not be
    possible for a manifest to reference blobs that were never pushed.
    '''
    # patched CD tar -> config referencing it.
    patched_cd_bytes = b'patched component descriptor content'
    raw_fobj = component_descriptor_to_tar(patched_cd_bytes)
    # hash what replicate_blob will compute for the file-like branch
    h = hashlib.sha256(raw_fobj.read())
    raw_fobj.seek(0)
    patched_cd_digest = f'sha256:{h.hexdigest()}'

    cfg = ocm.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=ocm.oci.ComponentDescriptorOciBlobRef(
            digest=patched_cd_digest,
            size=len(patched_cd_bytes),
            mediaType='application/vnd.gardener.cloud.cnudie.component-descriptor.v2+yaml+tar',
        ),
    )
    cfg_raw = json.dumps(cfg.componentDescriptorLayer.__dict__).encode()

    src_cd_layer = om.OciBlobRef(
        digest='sha256:' + 'cd' * 32,
        mediaType='application/vnd.gardener.cloud.cnudie.component-descriptor.v2+yaml+tar',
        size=len(patched_cd_bytes),
    )
    src_cfg = om.OciBlobRef(
        digest='sha256:' + 'cf' * 32,
        mediaType='application/vnd.gardener.cloud.cnudie.component.config.v1+json',
        size=len(cfg_raw),
    )
    src_manifest = om.OciImageManifest(config=src_cfg, layers=[src_cd_layer])

    client = _HeadOkPutRecords()
    tgt_manifest = oci.replicate_blobs(
        src_ref='src.example/comp:v1',
        src_oci_manifest=src_manifest,
        tgt_ref='tgt.example/comp:v1',
        oci_client=client,
        blob_overwrites={src_cd_layer: raw_fobj, src_cfg: cfg_raw},
    )

    # Even though head_manifest would say "present", the overwrite path must still
    # push both blobs.
    expected_cfg_digest = f'sha256:{hashlib.sha256(cfg_raw).hexdigest()}'
    assert expected_cfg_digest in client.put_blobs, (
        f'overwrite cfg blob was not pushed: {expected_cfg_digest} not in {client.put_blobs}'
    )
    assert patched_cd_digest in client.put_blobs, (
        f'overwrite cd blob was not pushed: {patched_cd_digest} not in {client.put_blobs}'
    )
    # And the returned manifest must reference the *pushed* config digest.
    assert tgt_manifest.config.digest == expected_cfg_digest
