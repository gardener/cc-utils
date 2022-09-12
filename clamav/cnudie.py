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
import clamav.scan
import dso.model
import oci.client

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


def scan_resources(
    component_resources: typing.Iterable[tuple[cm.Component, cm.Resource]],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
    s3_client=None,
    max_workers:int = 16,
) -> typing.Generator[clamav.scan.ClamAV_ResourceScanResult, None, None]:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    def scan_resource(component_resource: typing.Tuple[cm.Component, cm.Resource]):
        component, resource = component_resource

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
        )

        # pylint: disable=E1123
        return clamav.scan.ClamAV_ResourceScanResult(
            component=component,
            artifact=resource,
            scan_result=scan_result,
        )

    tasks = [
        executor.submit(scan_resource, component_resource)
       for component_resource in component_resources
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
    resource_scan_result: clamav.scan.ClamAV_ResourceScanResult,
    datasource: str = dso.model.Datasource.CLAMAV,
    datatype: str = dso.model.Datatype.MALWARE,
    creation_date: datetime.datetime = datetime.datetime.now(),
) -> dso.model.ArtefactMetadata:

    artefact_ref = dso.model.component_artefact_id_from_ocm(
        component=resource_scan_result.component,
        artefact=resource_scan_result.artifact,
    )

    meta = dso.model.Metadata(
        datasource=datasource,
        type=datatype,
        creation_date=creation_date,
    )

    finding = dso.model.MalwareSummary(
        findings=resource_scan_result.scan_result.findings,
    )

    return dso.model.ArtefactMetadata(
        artefact=artefact_ref,
        meta=meta,
        data=finding,
    )
