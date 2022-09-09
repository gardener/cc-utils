import concurrent.futures
import datetime
import functools
import logging
import socket
import tarfile
import traceback
import typing

import requests.exceptions

import ccc.oci
import clamav.client
import clamav.cnudie
import clamav.scan
import gci.componentmodel
import oci.client as oc
import oci.model as om
import product
import tarutil


logger = logging.getLogger(__name__)


def iter_image_files(
    image_reference: str,
    oci_client: oc.Client=None,
) -> typing.Iterable[typing.Tuple[typing.IO, str]]:
    '''
    returns a generator yielding the regular files contained in the specified oci-image
    as sequence of two-tuples (filelike-obj, <layer-digest:relpath>).

    The image's layer-blobs are retrieve in the order they are defined in the image-manifest.
    cfg-blobs are ignored. All layer-blobs are assued to be tarfiles (which is not necessarily
    a valid assumption for non-docker-compatible oci-artifacts).
    '''
    if not oci_client:
        oci_client = ccc.oci.oci_client()

    manifest = oci_client.manifest(image_reference=image_reference)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(manifest.layers))

    def _iter_layer_blob_files(layer_blob: om.OciBlobRef):
        blob_resp = oci_client.blob(
            image_reference=image_reference,
            digest=layer_blob.digest,
            stream=True,
        )

        fileobj = tarutil.FilelikeProxy(
            generator=blob_resp.iter_content(
                chunk_size=tarfile.RECORDSIZE,
                decode_unicode=False,
            ),
        )
        with tarfile.open(
            fileobj=fileobj,
            mode='r|*',
        ) as layer_tarfile:
            for tar_info in layer_tarfile:
                if not tar_info.isfile():
                    continue
                yield (
                    layer_tarfile.extractfile(tar_info),
                    f'{layer_blob.digest}:{tar_info.name}',
                )

    # we ignore cfg-blob (which would be included in manifest.blobs())
    for layer_blob_files in executor.map(_iter_layer_blob_files, manifest.layers):
        yield from layer_blob_files


def _scan_oci_image(
    clamav_client: clamav.client.ClamAVClient,
    oci_client: oc.Client,
    image_reference: str,
) -> typing.Generator[clamav.scan.MalwarescanResult, None, None]:
    start_time = datetime.datetime.now()
    logger.info(f'starting to scan {image_reference=}')

    try:
        content_iterator = iter_image_files(
            image_reference=image_reference,
            oci_client=oci_client,
        )
        findings = clamav_client.scan_container_image(
            content_iterator=content_iterator,
        )
        yield from findings
        passed_seconds = datetime.datetime.now().timestamp() - start_time.timestamp()
        logger.info(f'scan finished for {image_reference=} after {passed_seconds=}')
        return
    except tarfile.TarError as te:
        passed_seconds = datetime.datetime.now().timestamp() - start_time.timestamp()
        logger.warning(f'{image_reference=}: {te=} - falling back to layer-scan {passed_seconds=}')

    # fallback to layer-wise scan in case we encounter gzip-uncompression-problems
    def iter_layers():
        manifest = oci_client.manifest(image_reference=image_reference)
        for layer in manifest.layers:
            layer_blob = oci_client.blob(
                image_reference=image_reference,
                digest=layer.digest,
                stream=True,
            )
            yield (layer_blob.iter_content(chunk_size=4096), layer.digest)

    findings = clamav_client.scan_container_image(
        content_iterator=iter_layers(),
    )
    yield from findings

    passed_seconds = datetime.datetime.now().timestamp() - start_time.timestamp()
    logger.info(f'{image_reference=} layer-scan finished after {passed_seconds=}')


def _try_scan_image(
    oci_resource: gci.componentmodel.Resource,
    clamav_client: clamav.client.ClamAVClient,
    oci_client: oc.Client,
):
    access: gci.componentmodel.OciAccess = oci_resource.access

    try:
        clamav_findings = _scan_oci_image(
            clamav_client=clamav_client,
            oci_client=oci_client,
            image_reference=access.imageReference,
        )

        return clamav.scan.MalwarescanResult(
                resource=oci_resource,
                scan_state=clamav.scan.MalwareScanState.FINISHED_SUCCESSFULLY,
                findings=[
                    f'{path}: {scan_result.virus_signature()}'
                    for scan_result, path in clamav_findings
                ],
            )
    except (requests.exceptions.RequestException, socket.gaierror) as e:
        # log warning and include it as finding to document it via the generated report-mails
        warning = f'error while scanning {oci_resource.access.imageReference} {e=}'
        logger.warning(warning)
        traceback.print_exc()

        return clamav.scan.MalwarescanResult(
                resource=oci_resource,
                scan_state=clamav.scan.MalwareScanState.FINISHED_WITH_ERRORS,
                findings=[warning],
            )


def virus_scan_images(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
    filter_function,
    clamav_client: clamav.client.ClamAVClient,
    oci_client: oc.Client=None,
    max_workers=8,
) -> typing.Generator[clamav.scan.MalwarescanResult, None, None]:
    '''Scans components of the given Component Descriptor using ClamAV

    Used by image-scan-trait
    '''
    resources = [
        resource for component, resource
        in product.v2.enumerate_oci_resources(component_descriptor=component_descriptor_v2)
        if filter_function(component, resource)
    ]

    logger.info(f'will scan {len(resources)=} OCI Images')

    try_scan_func = functools.partial(
        _try_scan_image,
        clamav_client=clamav_client,
        oci_client=oci_client,
    )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    results = executor.map(
        try_scan_func,
        resources,
    )

    yield from results
