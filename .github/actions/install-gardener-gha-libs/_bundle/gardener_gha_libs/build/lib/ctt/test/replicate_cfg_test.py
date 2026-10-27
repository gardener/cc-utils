# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import dataclasses
import hashlib
import io
import json

import oci.model as om
import oci as ocimod
import ocm.oci


class _FakeClient:
    def __init__(self):
        self.pushed = {}

    def put_blob(self, image_reference, digest, octets_count, data):
        self.pushed[digest] = data.read() if hasattr(data, 'read') else data

    def head_blob(self, image_reference, digest):
        class R: ok = False
        return R()

    def mount_blob(self, **k):
        return False

    def blob(self, image_reference, digest):
        raise RuntimeError(f'unexpected blob fetch {digest}')


def _mk_manifest(
    cfg_digest: str,
    cfg_size: int,
    cd_digest: str,
    cd_size: int,
) -> om.OciImageManifest:
    return om.OciImageManifest(
        config=om.OciBlobRef(
            digest=cfg_digest,
            mediaType='application/vnd.gardener.cloud.cnudie.component.config.v1+json',
            size=cfg_size,
        ),
        layers=[om.OciBlobRef(
            digest=cd_digest,
            mediaType='application/vnd.gardener.cloud.cnudie.component-descriptor.v2+yaml+tar',
            size=cd_size,
        )],
    )


def test_replicate_blobs_returns_manifest_with_patched_config():
    src_cfg_raw = json.dumps({'componentDescriptorLayer': {
        'digest': 'sha256:' + '11' * 32, 'size': 10,
        'mediaType': 'application/vnd.gardener.cloud.cnudie.component-descriptor.v2+yaml+tar',
    }}).encode()
    src_cfg_digest = 'sha256:' + hashlib.sha256(src_cfg_raw).hexdigest()
    src_cd_digest = 'sha256:' + '11' * 32
    src = _mk_manifest(src_cfg_digest, len(src_cfg_raw), src_cd_digest, 10)

    patched_cd = b'PATCHED-CD-CONTENT'
    patched_cd_digest = 'sha256:' + hashlib.sha256(patched_cd).hexdigest()
    patched_cfg = json.dumps(dataclasses.asdict(ocm.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=ocm.oci.ComponentDescriptorOciBlobRef(
            digest=patched_cd_digest,
            size=len(patched_cd),
            mediaType='application/vnd.gardener.cloud.cnudie.component-descriptor.v2+yaml+tar',
        ),
    ))).encode()
    patched_cfg_digest = 'sha256:' + hashlib.sha256(patched_cfg).hexdigest()

    client = _FakeClient()
    tgt_manifest = ocimod.replicate_blobs(
        src_ref='src.example/comp:v1',
        src_oci_manifest=src,
        tgt_ref='tgt.example/comp:v1',
        oci_client=client,
        blob_overwrites={
            src.layers[0]: io.BytesIO(patched_cd),
            src.config: patched_cfg,
        },
    )

    # regression assertion: the returned manifest must reference the patched config,
    # not the source config. Wire manifest must equal json of returned manifest.
    assert tgt_manifest.config.digest == patched_cfg_digest, (
        f'manifest references stale config {tgt_manifest.config.digest} != {patched_cfg_digest}'
    )
    assert patched_cfg_digest in client.pushed
    assert tgt_manifest.layers[0].digest == patched_cd_digest

    wire = json.dumps(tgt_manifest.as_dict())
    assert json.loads(wire)['config']['digest'] == patched_cfg_digest
