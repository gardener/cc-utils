import datetime
import logging
import tarfile
import tempfile
import typing

import gci.componentmodel as cm

import ci.log
import ci.util
import delivery.client
import delivery.model
import delivery.util
import dso.model as dm
import github.compliance.model as gcm
import github.compliance.issue as gciss
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
) -> typing.Generator[gcm.OsIdScanResult, None, None]:
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
) -> gcm.OsIdScanResult:
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

    # pylint: disable=E1123
    return gcm.OsIdScanResult(
        component=component,
        artifact=resource,
        os_id=os_info,
    )


def scan_result_group_collection_for_outdated_os_ids(
    results: tuple[gcm.OsIdScanResult],
    delivery_svc_client: delivery.client.DeliveryServiceClient,
):
    os_names = {r.os_id.ID for r in results if r.os_id.ID}
    os_infos = {
        os_name: info
        for os_name in os_names
        if (info := delivery_svc_client.os_release_infos(os_id=os_name, absent_ok=True)) is not None
    }

    def classification_callback(result: gcm.OsIdScanResult):
        os_id = result.os_id
        if not os_id.ID in os_infos:
            return None

        if delivery.util.branch_reached_eol(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
        ):
            return gcm.Severity.HIGH
        elif delivery.util.update_available(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
        ):
            return gcm.Severity.MEDIUM
        else:
            return None

    def findings_callback(result: gcm.OsIdScanResult):
        os_id = result.os_id
        if not os_id.ID in os_infos:
            return None

        relation = result.artifact.relation
        if not relation is cm.ResourceRelation.LOCAL:
            logger.info(f'{result.artifact.name=} is not "local" - will ignore findings')
            return False

        if delivery.util.branch_reached_eol(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
        ):
            return True
        elif delivery.util.update_available(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
        ):
            return True
        else:
            return False

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=gciss._label_os_outdated,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )


def upload_to_delivery_db(
    db_client: delivery.client.DeliveryServiceClient,
    resource: cm.Resource,
    component: cm.Component,
    os_info: um.OperatingSystemId,
):
    artefact_ref = dm.component_artefact_id_from_ocm(
        component=component,
        artefact=resource,
    )
    meta = dm.Metadata(
        datasource=dm.Datasource.CC_UTILS,
        type=dm.Datatype.OS_IDS,
        creation_date=datetime.datetime.now()
    )

    os_id = dm.OsID(
        os_info=os_info,
    )
    artefact_metadata = dm.ArtefactMetadata(
        artefact=artefact_ref,
        meta=meta,
        data=os_id,
    )

    db_client.upload_metadata(data=artefact_metadata)
