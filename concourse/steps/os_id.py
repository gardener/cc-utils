import datetime
import logging
import tarfile
import tempfile
import typing

import dacite

import gci.componentmodel as cm

import ci.log
import ci.util
import delivery.client
import dso.model as dm
import oci.client
import oci.model
import product.v2
import unixutil.scan as us
import unixutil.model as um

logger = logging.getLogger('os-id')
ci.log.configure_default_logging()


def determine_os_ids(
    component_descriptor: cm.ComponentDescriptor,
    oci_client: oci.client.Client,
) -> typing.Generator[tuple[cm.Component, cm.Resource, um.OperatingSystemId], None, None]:
    component_resources: typing.Generator[tuple[cm.Component, cm.Resource], None, None] = \
        product.v2.enumerate_oci_resources(component_descriptor=component_descriptor)

    for component, resource in component_resources:
        yield base_image_os_id(
            oci_client=oci_client,
            component=component,
            resource=resource,
        )


def base_image_os_id(
    oci_client: oci.client.Client,
    component: cm.Component,
    resource: cm.Resource,
) -> tuple[cm.Component, cm.Resource, um.OperatingSystemId]:
    image_reference = resource.access.imageReference

    manifest = oci_client.manifest(image_reference=image_reference)

    # if multi-arch, randomly choose first entry (assumption: all variants have same os/version)
    if isinstance(manifest, oci.model.OciImageManifestList):
        manifest: oci.model.OciImageManifestList
        manifest: oci.model.OciBlobRef = manifest.manifests[0]
        image_reference = oci.model.OciImageReference(image_reference)
        manifest = oci_client.manifest(image_reference.ref_without_tag + '@' + manifest.digest)

    first_layer_blob = oci_client.blob(
        image_reference=image_reference,
        digest=manifest.layers[0].digest,
    )

    # workaround streaming issues -> always write to tempfile :/
    with tempfile.TemporaryFile() as tempf:
        for chunk in first_layer_blob.iter_content(chunk_size=4096):
            tempf.write(chunk)
        tempf.seek(0)

        tf = tarfile.open(fileobj=tempf, mode='r')

        os_info = us.determine_osinfo(tarfh=tf)

    return component, resource, os_info


def upload_to_delivery_db(
    db_client: delivery.client.DeliveryServiceClient,
    resource: cm.Resource,
    component: cm.Component,
    os_info: um.OperatingSystemId,
):
    artefact_ref = dm.artefact_ref_from_ocm(
        component=component,
        artefact=resource,
    )
    meta = dm.Metadata(
        datasource=dm.Datasource.CC_UTILS,
        type=dm.Datatype.OS_IDS,
        creation_date=datetime.datetime.now()
    )

    os_info = dacite.from_dict(
        data_class=dm.OsInfo,
        data=os_info,
    )
    os_id = dm.OsID(
        osInfo=os_info,
    )
    artefact_metadata = dm.ArtefactMetadata(
        artefact=artefact_ref,
        meta=meta,
        data=os_id,
    )

    db_client.upload_metadata(data=artefact_metadata)
