import dataclasses
import enum
import hashlib
import json
import typing

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


def _oci_blob_ref_from_access(
    access: cm.LocalBlobAccess,
    oci_client: oci.client.Client=None,
    oci_image_reference: om.OciImageReference=None,
) -> om.OciBlobRef:
    '''
    create OciBlobRef from LocalBlobAccess. If access has a globalAccess attribute, all
    values will be read from it and returned.
    Otherwise, the `size` attribute will be looked up using the (then required) oci_client. A
    blob with a digest matching the one from access.localReference (typically of form
    sha256:<hexdigest>) must be present at the given oci_image_reference.

    This function is useful for creating OCI Image Manifest layer entries from local blob access
    objects from Component Descriptors.
    '''
    if (global_access := access.globalAccess):
        return om.OciBlobRef(
            digest=global_access.digest,
            mediaType=global_access.mediaType,
            size=global_access.size,
        )
    if not oci_client or not oci_image_reference:
        raise ValueError(
            'oci_client and oci_image_reference must both be set if no globalAccess is present',
            access,
        )

    res = oci_client.head_blob(
        image_reference=oci_image_reference,
        digest=access.localReference,
        absent_ok=True,
    )

    if not res.ok:
        if res.status_code == 404:
            raise ValueError(
                f'{access.localReference=} not present at {oci_image_reference=}',
                access,
            )
        res.raise_for_status()

    length = int(res.headers.get('content-length'))

    return om.OciBlobRef(
        digest=access.localReference,
        mediaType=access.mediaType,
        size=length,
    )


def _iter_oci_blob_refs(
    component: cm.Component,
    oci_client: oci.client.Client=None,
    oci_image_reference: om.OciImageReference=None,
) -> typing.Generator[None, None, om.OciBlobRef]:
    for artefact in component.iter_artefacts():
        access = artefact.access
        if not isinstance(access, cm.LocalBlobAccess):
            continue

        blob_ref = _oci_blob_ref_from_access(
            access=access,
            oci_client=oci_client,
            oci_image_reference=oci_image_reference,
        )
        yield blob_ref


def upload_component_descriptor(
    component_descriptor: cm.ComponentDescriptor | cm.Component,
    on_exist:UploadMode|str=UploadMode.SKIP,
    ocm_repository: cm.OciOcmRepository | str = None,
    oci_client: oci.client.Client=None,
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

    component = component_descriptor.component

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

        if not component.current_repository_ctx() == ocm_repository:
            component.repositoryContexts.append(ocm_repository)

    target_ref = cnudie.util.oci_artefact_reference(component)

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

    local_blob_layers = { # use set for deduplication
        blob_ref for blob_ref in
        _iter_oci_blob_refs(
            component=component,
            oci_client=oci_client,
            oci_image_reference=target_ref,
        )
    }

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
        ] + list(local_blob_layers),
    )

    manifest_dict = manifest.as_dict()
    manifest_bytes = json.dumps(manifest_dict).encode('utf-8')

    oci_client.put_manifest(
        image_reference=target_ref,
        manifest=manifest_bytes,
    )

    return manifest_bytes
