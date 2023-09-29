import datetime
import logging
import tarfile
import typing

import gci.componentmodel as cm

import ci.log
import ci.util
import cnudie.iter
import cnudie.retrieve
import delivery.client
import delivery.model
import delivery.util
import dso.model as dm
import github.compliance.issue as gciss
import github.compliance.model as gcm
import oci.client
import oci.model
import tarutil
import unixutil.model as um
import unixutil.scan as us

logger = logging.getLogger('os-id')
ci.log.configure_default_logging()


def determine_os_ids(
    component_descriptor: cm.ComponentDescriptor,
    oci_client: oci.client.Client,
    lookup: cnudie.retrieve.ComponentDescriptorLookupById,
    delivery_service_client: delivery.client.DeliveryServiceClient,
) -> typing.Generator[gcm.OsIdScanResult, None, None]:
    for component, resource in cnudie.iter.iter_resources(
        component=component_descriptor,
        lookup=lookup,
    ):
        if resource.type != cm.ArtefactType.OCI_IMAGE:
            continue

        if not resource.access:
            continue

        if resource.access.type != cm.AccessType.OCI_REGISTRY:
            continue

        yield base_image_os_id(
            oci_client=oci_client,
            delivery_service_client=delivery_service_client,
            component=component,
            resource=resource,
        )


def base_image_os_id(
    oci_client: oci.client.Client,
    delivery_service_client: delivery.client.DeliveryServiceClient,
    component: cm.Component,
    resource: cm.Resource,
) -> gcm.OsIdScanResult:
    # shortcut scan if there is already a scan-result
    if delivery_service_client:
        scan_result = delivery_service_client.metadata(
            component=component,
            artefact=resource,
            types=('os_ids',),
        )
        scan_result = tuple(scan_result)
        if len(scan_result) > 1:
            logger.warning(
                f'found more than one scanresult for {component.name}:{resource.name}'
            )
            scan_result = scan_result[0]
        if scan_result:
            scan_result = scan_result[0]

            if not isinstance(scan_result.data, dm.OsID):
                logger.warning('unexpected scan-result-type: {scan_result=}')
            else:
                scan_result.data: dm.OsID

                return gcm.OsIdScanResult(
                    scanned_element=cnudie.iter.ResourceNode(
                        path=(component,),
                        resource=resource,
                    ),
                    os_id=scan_result.data.os_info,
                    skip_upload_to_deliverydb=True, # no need to re-upload (got it from there)
                )

    # there was no scanresult, so we have to scan
    image_reference = resource.access.imageReference

    manifest = oci_client.manifest(
        image_reference=image_reference,
        accept=oci.model.MimeTypes.prefer_multiarch,
    )

    # if multi-arch, randomly choose first entry (assumption: all variants have same os/version)
    if isinstance(manifest, oci.model.OciImageManifestList):
        manifest: oci.model.OciImageManifestList
        manifest: oci.model.OciBlobRef = manifest.manifests[0]
        image_reference = oci.model.OciImageReference(image_reference)
        manifest = oci_client.manifest(image_reference.ref_without_tag + '@' + manifest.digest)

    last_os_info = None

    for layer in manifest.layers:
        layer_blob = oci_client.blob(
            image_reference=image_reference,
            digest=layer.digest,
        )
        fileproxy = tarutil.FilelikeProxy(
            layer_blob.iter_content(chunk_size=tarfile.BLOCKSIZE)
        )
        tf = tarfile.open(fileobj=fileproxy, mode='r|*')
        if (os_info := us.determine_osinfo(tf)):
            last_os_info = os_info

    if not last_os_info:
        # if we could not determine os-info, upload a dummy os-info (with all entries set to None)
        # to keep track of the failed scan attempt
        last_os_info = um.OperatingSystemId()

    # pylint: disable=E1123
    return gcm.OsIdScanResult(
        scanned_element=cnudie.iter.ResourceNode(
            path=(component,),
            resource=resource,
        ),
        os_id=last_os_info,
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

        if os_id.is_distroless: # hardcode: never require distroless-images to be updated
            return None

        if delivery.util.branch_reached_eol(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
        ):
            return gcm.Severity.HIGH
        elif delivery.util.update_available(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
            ignore_if_patchlevel_is_next_to_greatest=True,
        ):
            return gcm.Severity.MEDIUM
        else:
            return None

    def findings_callback(result: gcm.OsIdScanResult):
        os_id = result.os_id
        if not os_id.ID in os_infos:
            return None

        if os_id.is_distroless: # hardcode: never require distroless-images to be updated
            return None

        relation = result.scanned_element.resource.relation

        if delivery.util.branch_reached_eol(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
        ):
            return True

        if not relation is cm.ResourceRelation.LOCAL:
            logger.info(f'{result.scanned_element.resource.name=} '
                f'is not "local" - will ignore findings')
            return False

        if delivery.util.update_available(
            os_id=os_id,
            os_infos=os_infos[os_id.ID],
            ignore_if_patchlevel_is_next_to_greatest=True,
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

    db_client.upload_metadata(data=[artefact_metadata])
