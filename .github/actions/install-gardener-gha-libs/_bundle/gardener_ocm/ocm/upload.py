'''
functionality for uploading OCM-Component-Descriptors to OCI Registries.

Note: None of the Symbols defined in this module is intended as a stable API
      -> expect incompatible changes w/o prior notice
'''

import collections.abc
import dataclasses
import enum
import hashlib
import json

import oci.client
import oci.model as om
import ocm
import ocm.oci


class UploadMode(enum.StrEnum):
    SKIP = 'skip'
    FAIL = 'fail'
    OVERWRITE = 'overwrite'


def _oci_blob_ref_from_access(
    access: ocm.LocalBlobAccess,
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
    component: ocm.Component,
    oci_client: oci.client.Client=None,
    oci_image_reference: om.OciImageReference=None,
) -> collections.abc.Generator[None, None, om.OciBlobRef]:
    for artefact in component.iter_artefacts():
        access = artefact.access
        if not isinstance(access, ocm.LocalBlobAccess):
            continue

        blob_ref = _oci_blob_ref_from_access(
            access=access,
            oci_client=oci_client,
            oci_image_reference=oci_image_reference,
        )
        yield blob_ref


def upload_component_descriptor(
    component_descriptor: ocm.ComponentDescriptor | ocm.Component,
    oci_client: oci.client.Client,
    on_exist: UploadMode | str=UploadMode.SKIP,
    ocm_repository: ocm.OciOcmRepository | str=None,
):
    on_exist = UploadMode(on_exist)

    if isinstance(component_descriptor, ocm.Component):
        component_descriptor = ocm.ComponentDescriptor(
            component=component_descriptor,
            meta=ocm.Metadata(),
            signatures=[],
        )

    component = component_descriptor.component
    schema_version = ocm.SchemaVersion(component_descriptor.meta.schemaVersion)

    if not schema_version is ocm.SchemaVersion.V2:
        raise RuntimeError(f'unsupported component-descriptor-version: {schema_version=}')

    if ocm_repository:
        if isinstance(ocm_repository, str):
            ocm_repository = ocm.OciOcmRepository(baseUrl=ocm_repository)
        elif isinstance(ocm_repository, ocm.OciOcmRepository):
            pass
        else:
            raise TypeError(type(ocm_repository))

        if not component.current_ocm_repo == ocm_repository:
            component.repositoryContexts.append(ocm_repository)
    else:
        ocm_repository = component.current_ocm_repo

    target_ref = ocm_repository.component_version_oci_ref(component)

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

    raw_fobj = ocm.oci.component_descriptor_to_tarfileobj(component_descriptor)
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

    cfg = ocm.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=ocm.oci.ComponentDescriptorOciBlobRef(
            digest=cd_digest_with_alg,
            size=cd_octets,
            mediaType=ocm.oci.component_descriptor_mimetype,
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
        config=ocm.oci.ComponentDescriptorOciCfgBlobRef(
            digest=cfg_digest_with_alg,
            size=cfg_octets,
            mediaType=ocm.oci.component_descriptor_cfg_mimetype,
        ),
        layers=[
            ocm.oci.ComponentDescriptorOciBlobRef(
                digest=cd_digest_with_alg,
                size=cd_octets,
                mediaType=ocm.oci.component_descriptor_mimetype,
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
