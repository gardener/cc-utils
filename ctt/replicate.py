'''
utils used for replicating cnudie components between different repository contexts
'''


import dataclasses
import hashlib
import json
import logging

import gci.oci

import ccc.oci
import gci.componentmodel as cm
import oci
import oci.model as om

logger = logging.getLogger(__name__)


def replicate_oci_artifact_with_patched_component_descriptor(
    src_name: str,
    src_version: str,
    patched_component_descriptor: gci.componentmodel.ComponentDescriptor,
    src_ctx_repo: cm.OciOcmRepository,
):
    if not isinstance(src_ctx_repo, cm.OciOcmRepository):
        raise NotImplementedError(src_ctx_repo)

    client = ccc.oci.oci_client()

    component = patched_component_descriptor.component
    target_repository = component.current_repository_ctx()
    target_ref = target_repository.component_version_oci_ref(component)

    if client.head_manifest(image_reference=target_ref, absent_ok=True):
        # do not overwrite existing component-descriptors
        return

    src_ref = src_ctx_repo.component_version_oci_ref(
        name=src_name,
        version=src_version,
    )

    src_manifest = client.manifest(
        image_reference=src_ref
    )

    raw_fobj = gci.oci.component_descriptor_to_tarfileobj(patched_component_descriptor)

    cd_digest = hashlib.sha256()
    while (chunk := raw_fobj.read(4096)):
        cd_digest.update(chunk)

    cd_octets = raw_fobj.tell()
    cd_digest = cd_digest.hexdigest()
    cd_digest_with_alg = f'sha256:{cd_digest}'
    raw_fobj.seek(0)

    # src component descriptor OciBlobRef for patching
    src_config_dict = json.loads(client.blob(src_ref, src_manifest.config.digest).content)
    src_component_descriptor_oci_blob_ref = om.OciBlobRef(
        **src_config_dict['componentDescriptorLayer'],
    )

    # config OciBlobRef
    cfg = gci.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=gci.oci.ComponentDescriptorOciBlobRef(
            digest=cd_digest_with_alg,
            size=cd_octets,
        ),
    )
    cfg_raw = json.dumps(dataclasses.asdict(cfg)).encode('utf-8')

    # replicate all blobs except overwrites
    target_manifest = oci.replicate_blobs(
        src_ref=src_ref,
        src_oci_manifest=src_manifest,
        tgt_ref=target_ref,
        oci_client=client,
        blob_overwrites={
            src_component_descriptor_oci_blob_ref: raw_fobj,
            src_manifest.config: cfg_raw,
        },
    )

    target_manifest_dict = dataclasses.asdict(target_manifest)
    target_manifest_bytes = json.dumps(target_manifest_dict).encode('utf-8')

    client.put_manifest(
        image_reference=target_ref,
        manifest=target_manifest_bytes,
    )
