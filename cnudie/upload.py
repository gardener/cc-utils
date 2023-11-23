import dataclasses
import enum
import hashlib
import json

import ccc.oci
import cnudie.util
import gci.componentmodel as cm
import gci.oci
import oci.client
import oci.model as om


class UploadMode(enum.StrEnum):
    SKIP = 'skip'
    FAIL = 'fail'
    OVERWRITE = 'overwrite'


def upload_component_descriptor(
    component_descriptor: cm.ComponentDescriptor | cm.Component,
    on_exist:UploadMode|str=UploadMode.SKIP,
    ocm_repository: cm.OciOcmRepository | str = None,
    oci_client: oci.client.Client=None,
    extra_layers: list[gci.oci.OciBlobRef]=[],
):
    if not oci_client:
        oci_client = ccc.oci.oci_client()

    on_exist = UploadMode(on_exist)

    if isinstance(component_descriptor, cm.Component):
        component_descriptor = cm.ComponentDescriptor(
            component=component_descriptor,
            meta=cm.Metadata(),
            signatures=[],
        )

    schema_version = component_descriptor.meta.schemaVersion
    if not schema_version is cm.SchemaVersion.V2:
        raise RuntimeError(f'unsupported component-descriptor-version: {schema_version=}')

    if ocm_repository:
        if isinstance(ocm_repository, str):
            ocm_repository = cm.OciOcmRepository(baseUrl=ocm_repository)
        elif isinstance(ocm_repository, cm.OciOcmRepository):
            pass
        else:
            raise TypeError(type(ocm_repository))

        if  not component_descriptor.component.current_repository_ctx() == ocm_repository:
            component_descriptor.component.repositoryContexts.append(ocm_repository)

    target_ref = cnudie.util.oci_artefact_reference(component_descriptor.component)

    if on_exist in (UploadMode.SKIP, UploadMode.FAIL):
        # check whether manifest exists (head_manifest does not return None)
        if oci_client.head_manifest(image_reference=target_ref, absent_ok=True):
            if on_exist is UploadMode.SKIP:
                return
            if on_exist is UploadMode.FAIL:
                # XXX: we might still ignore it, if the to-be-uploaded CD is equal to the existing
                # one
                raise ValueError(f'{target_ref=} already existed')
    elif on_exist is UploadMode.OVERWRITE:
        pass
    else:
        raise NotImplementedError(on_exist)

    raw_fobj = gci.oci.component_descriptor_to_tarfileobj(component_descriptor)
    cd_digest = hashlib.sha256()
    while (chunk := raw_fobj.read(4096)):
        cd_digest.update(chunk)

    cd_octets = raw_fobj.tell()
    cd_digest = cd_digest.hexdigest()
    cd_digest_with_alg = f'sha256:{cd_digest}'
    raw_fobj.seek(0)

    oci_client.put_blob(
        image_reference=target_ref,
        digest=cd_digest_with_alg,
        octets_count=cd_octets,
        data=raw_fobj,
    )

    cfg = gci.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=gci.oci.ComponentDescriptorOciBlobRef(
            digest=cd_digest_with_alg,
            size=cd_octets,
        )
    )
    cfg_raw = json.dumps(dataclasses.asdict(cfg)).encode('utf-8')
    cfg_octets = len(cfg_raw)
    cfg_digest = hashlib.sha256(cfg_raw).hexdigest()
    cfg_digest_with_alg = f'sha256:{cfg_digest}'

    oci_client.put_blob(
        image_reference=target_ref,
        digest=cfg_digest_with_alg,
        octets_count=cfg_octets,
        data=cfg_raw,
    )

    manifest = om.OciImageManifest(
        config=gci.oci.ComponentDescriptorOciCfgBlobRef(
            digest=cfg_digest_with_alg,
            size=cfg_octets,
        ),
        layers=[
            gci.oci.ComponentDescriptorOciBlobRef(
                digest=cd_digest_with_alg,
                size=cd_octets,
            ),
        ] + extra_layers,
    )

    manifest_dict = manifest.as_dict()
    manifest_bytes = json.dumps(manifest_dict).encode('utf-8')

    oci_client.put_manifest(
        image_reference=target_ref,
        manifest=manifest_bytes,
    )
