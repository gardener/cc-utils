import concurrent.futures
import datetime
import enum
import logging
import tarfile
import typing

import gci.componentmodel as cm

import ci.log
import ci.util
import clamav.client
import clamav.model
import clamav.scan
import cnudie.iter
import dso.model
import github.compliance.model
import oci.client

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


def scan_resources(
    resource_nodes: typing.Iterable[cnudie.iter.ResourceNode],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
    clamav_version_info: clamav.model.ClamAVVersionInfo,
    s3_client=None,
    max_workers:int = 16,
) -> typing.Generator[clamav.model.ClamAVResourceScanResult, None, None]:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    def scan_resource(
        resource_node: cnudie.iter.ResourceNode,
    ):
        component = resource_node.component
        resource = resource_node.resource

        if isinstance(resource.access, cm.OciAccess):
            access: cm.OciAccess = resource.access
            image_reference = access.imageReference
            results = clamav.scan.scan_oci_image(
                image_reference=image_reference,
                oci_client=oci_client,
                clamav_client=clamav_client,
            )
        elif isinstance(resource.access, cm.S3Access):
            access: cm.S3Access = resource.access
            if isinstance(resource.type, enum.Enum):
                rtype = resource.type.value
            else:
                rtype = resource.type
            if not rtype.startswith('application/tar'):
                raise NotImplementedError(resource.type)

            fileobj = s3_client.Object(
                access.bucketName,
                access.objectKey,
            ).get()['Body']

            tf = tarfile.open(fileobj=fileobj, mode='r|*')

            results = clamav.scan.scan_tarfile(
                clamav_client=clamav_client,
                tf=tf,
            )
        else:
            raise NotImplementedError(type(resource.access))

        scan_result = clamav.scan.aggregate_scan_result(
            resource=resource,
            results=results,
            name=f'{component.name}/{resource.name}',
            clamav_version_info=clamav_version_info,
        )

        # pylint: disable=E1123
        return clamav.model.ClamAVResourceScanResult(
            scan_result=scan_result,
            scanned_element=cnudie.iter.ResourceNode(
                path=(component,),
                resource=resource,
            ),
        )

    tasks = [
        executor.submit(scan_resource, resource_node)
       for resource_node in resource_nodes
    ]

    logger.info(f'will scan {len(tasks)=} with {max_workers=}')

    while True:
        done, not_done = concurrent.futures.wait(
            tasks,
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        logger.info(f'{len(done)} scans finished - {len(not_done)=}')
        yield from (f.result() for f in done)
        if not not_done:
            break
        tasks = not_done


def resource_scan_result_to_artefact_metadata(
    resource_scan_result: clamav.model.ClamAVResourceScanResult,
    datasource: str = dso.model.Datasource.CLAMAV,
    datatype: str = dso.model.Datatype.MALWARE,
    creation_date: datetime.datetime = datetime.datetime.now(),
) -> dso.model.ArtefactMetadata:

    artefact = github.compliance.model.artifact_from_node(resource_scan_result.scanned_element)
    artefact_ref = dso.model.component_artefact_id_from_ocm(
        component=resource_scan_result.scanned_element.component,
        artefact=artefact,
    )

    meta = dso.model.Metadata(
        datasource=datasource,
        type=datatype,
        creation_date=creation_date,
    )

    def _scan_result_to_malware_finding(
            scan_result: clamav.model.ScanResult,
    ) -> dso.model.MalwareFinding:
        if (meta := scan_result.meta):
            meta = dso.model.MalwareFindingMeta(
                receive_duration_seconds=meta.receive_duration_seconds,
                scanned_octets=meta.scanned_octets,
                scanned_content_digest=meta.scanned_content_digest,
                scan_duration_seconds=meta.scan_duration_seconds,
            )
        return dso.model.MalwareFinding(
            status=scan_result.status.value,
            details=scan_result.details,
            meta=meta,
            name=scan_result.name,
            malware_status=scan_result.malware_status.name,
        )

    aggregated_scan_result = resource_scan_result.scan_result
    clamav_version_info = aggregated_scan_result.clamav_version_info

    finding = dso.model.MalwareSummary(
        findings=tuple(
            _scan_result_to_malware_finding(r)
            for r in aggregated_scan_result.findings
        ),
        metadata=dso.model.ClamAVMetadata(
            clamav_version_str=clamav_version_info.clamav_version_str,
            signature_version=clamav_version_info.signature_version,
            virus_definition_timestamp=clamav_version_info.signature_date,
        )
    )

    return dso.model.ArtefactMetadata(
        artefact=artefact_ref,
        meta=meta,
        data=finding,
    )
