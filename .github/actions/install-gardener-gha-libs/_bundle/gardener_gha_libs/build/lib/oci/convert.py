import dataclasses
import hashlib
import json

import dacite

import oci.client as oc
import oci.docker as od
import oci.model as om


def v2_cfg_from_v1_manifest(
    manifest: om.OciImageManifestV1,
) -> od.DockerCfg:
    # we only need the latest cfg
    history = manifest.history[0]
    docker_cfg = history['v1Compatibility']
    docker_cfg = json.loads(docker_cfg)

    root_fs = {
        'diff_ids': [
            layer.digest for layer in manifest.layers
        ],
        'type': 'layers',
    }
    docker_cfg['rootfs'] = root_fs

    return dacite.from_dict(
        data_class=od.DockerCfg,
        data=docker_cfg,
        config=dacite.Config(
            cast=[tuple],
        ),
    )


def v1_manifest_to_v2(
    manifest: om.OciImageManifestV1,
    oci_client: oc.Client,
    tgt_image_ref: str,
) -> om.OciImageManifest:
    docker_cfg = v2_cfg_from_v1_manifest(manifest=manifest)
    docker_cfg = dataclasses.asdict(docker_cfg)
    docker_cfg = json.dumps(docker_cfg).encode('utf-8')

    cfg_digest = f'sha256:{hashlib.sha256(docker_cfg).hexdigest()}'
    cfg_leng = len(docker_cfg)

    oci_client.put_blob(
        image_reference=tgt_image_ref,
        digest=cfg_digest,
        octets_count=cfg_leng,
        data=docker_cfg,
    )

    manifest_v2 = om.OciImageManifest(
        config=om.OciBlobRef(
            digest=cfg_digest,
            mediaType='application/vnd.docker.container.image.v1+json',
            size=cfg_leng,
        ),
        layers=manifest.layers,
    )

    return manifest_v2, docker_cfg
