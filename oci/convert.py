import dataclasses
import hashlib
import json
import tarfile
import zlib

import ccc.oci
import dacite

import oci.client as oc
import oci.docker as od
import oci.model as om


def v2_cfg_from_v1_manifest(
    manifest: om.OciImageManifestV1,
    src_image_reference: str
) -> od.DockerCfg:
    # we only need the latest cfg
    history = manifest.history[0]
    docker_cfg = history['v1Compatibility']
    docker_cfg = json.loads(docker_cfg)

    # calcuate hash of layer blobs.
    # Ungzip those images to generate the hash of the uncompressed image.
    oci_client =  ccc.oci.oci_client()
    uncompressed_layers_digests = []
    for layer in manifest.layers:
        decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)  # gzip format
        cfg_hash = hashlib.sha256() # we need to write "non-gzipped" hash to cfg-blob

        src_content = oci_client.blob(
            image_reference=src_image_reference,
            digest=layer.digest,
            stream=False,
        ).iter_content(chunk_size=tarfile.BLOCKSIZE * 64)

        is_first_chunk = True
        is_gziped = False
        for chunk in src_content:
            if is_first_chunk:
                is_first_chunk = False
                if bytes(chunk).startswith(b'\x1f\x8b'):
                    is_gziped = True
            if is_gziped:
                cfg_hash.update(decompressor.decompress(chunk))
            else:
                cfg_hash.update(chunk)

        uncompressed_layers_digests.append(f'sha256:{cfg_hash.hexdigest()}')

    # docker mandates the uncompressed-layer digests in the config
    root_fs = {
        'diff_ids': uncompressed_layers_digests,
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
    src_image_reference: str,
    tgt_image_ref: str,
) -> om.OciImageManifest:
    docker_cfg = v2_cfg_from_v1_manifest(manifest=manifest, src_image_reference=src_image_reference)
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
