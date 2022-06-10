import concurrent.futures
import dataclasses
import datetime
import logging
import typing

import gci.componentmodel as cm

import ci.log
import ci.util
import clamav.client
import clamav.scan
import dso.model
import oci.client
import product.v2

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


@dataclasses.dataclass
class ResourceScanResult:
    component: cm.Component
    resource: cm.Resource
    scan_result: clamav.scan.ImageScanResult


def scan_resources(
    component: typing.Union[cm.ComponentDescriptor, cm.Component],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
    max_workers:int = 16,
) -> typing.Generator[ResourceScanResult, None, None]:
    if isinstance(component, cm.ComponentDescriptor):
        component = component.component

    component_resources = product.v2.enumerate_oci_resources(
        component_descriptor=component,
    ) # Generator[Tuple[component, resource]]

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    def scan_resource(component_resource: typing.Tuple[cm.Component, cm.Resource]):
        component, resource = component_resource

        if not isinstance(resource.access, cm.OciAccess):
            raise NotImplementedError(type(resource.access))

        access: cm.OciAccess = resource.access
        image_reference = access.imageReference

        scan_result = clamav.scan.aggregate_scan_result(
            image_reference=image_reference,
            results=clamav.scan.scan_oci_image(
                image_reference=image_reference,
                oci_client=oci_client,
                clamav_client=clamav_client,
            ),
            name=f'{component.name}/{resource.name}',
        )

        return ResourceScanResult(
            component=component,
            resource=resource,
            scan_result=scan_result,
        )

    tasks = [
        executor.submit(scan_resource, component_resource)
       for component_resource in component_resources
    ]

    logger.info(f'will scan {len(tasks)=} for {component.name=} with {max_workers=}')

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
    resource_scan_result: ResourceScanResult,
    datasource: str = dso.model.Datasource.CLAMAV,
    datatype: str = dso.model.Datatype.MALWARE,
    creation_date: datetime.datetime = datetime.datetime.now(),
) -> dso.model.ArtefactMetadata:

    artefact_ref = dso.model.component_artefact_id_from_ocm(
        component=resource_scan_result.component,
        artefact=resource_scan_result.resource,
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
