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
import product.v2 as v2

logger = logging.getLogger(__name__)


def replicate_oci_artifact_with_patched_component_descriptor(
    src_name: str,
    src_version: str,
    patched_component_descriptor: gci.componentmodel.ComponentDescriptor,
    src_ctx_repo_base_url: str=None,
    src_ctx_repo: cm.RepositoryContext=None,
    on_exist=v2.UploadMode.SKIP,
):
    if not (bool(src_ctx_repo_base_url) ^ bool(src_ctx_repo)):
        raise ValueError('exactly one of src_ctx_repo, src_ctx_repo_base_url must be passed')

    if src_ctx_repo_base_url:
        logger.warning('passing src_ctx_repo_base_url is deprecated - pass src_ctx_repo')
        src_ctx_repo = cm.OciRepositoryContext(
            baseUrl=src_ctx_repo_base_url,
            componentNameMapping=cm.OciComponentNameMapping.URL_PATH,
        )

    if not isinstance(src_ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(src_ctx_repo)

    v2.ensure_is_v2(patched_component_descriptor)
    client = ccc.oci.oci_client()

    target_ref = v2._target_oci_ref(patched_component_descriptor.component)

    if on_exist in (v2.UploadMode.SKIP, v2.UploadMode.FAIL):
        # check whether manifest exists (head_manifest does not return None)
        if client.head_manifest(image_reference=target_ref, absent_ok=True):
            if on_exist is v2.UploadMode.SKIP:
                return
            if on_exist is v2.UploadMode.FAIL:
                # XXX: we might still ignore it, if the to-be-uploaded CD is equal to the existing
                # one
                raise ValueError(f'{target_ref=} already existed')
    elif on_exist is v2.UploadMode.OVERWRITE:
        pass
    else:
        raise NotImplementedError(on_exist)

    src_ref = v2._target_oci_ref_from_ctx_base_url(
        component_name=src_name,
        component_version=src_version,
        ctx_repo=src_ctx_repo,
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
