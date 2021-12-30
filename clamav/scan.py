import concurrent.futures
import dataclasses
import functools
import logging
import tarfile
import tempfile
import typing

import ci.log
import clamav.client
import oci.client
import oci.model


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


@dataclasses.dataclass
class ImageScanResult:
    '''
    overall (aggregated) scan result for an OCI Image
    '''
    image_reference: str
    name: str
    malware_status: clamav.client.MalwareStatus
    findings: typing.Collection[clamav.client.ScanResult] # if empty, there were no findings
    scan_count: int # amount of scanned files
    scanned_octets: int
    scan_duration_seconds: float
    upload_duration_seconds: float


def aggregate_scan_result(
    image_reference,
    results: typing.Iterable[clamav.client.ScanResult],
    name: str=None,
) -> ImageScanResult:
    count = 0
    succeeded = True
    scanned_octets = 0
    scan_duration_seconds = 0
    upload_duration_seconds = 0
    findings = []

    for result in results:
        count += 1
        if result.status is clamav.client.ScanStatus.SCAN_FAILED:
            succeeded = False
            continue

        scanned_octets += result.meta.scanned_octets
        scan_duration_seconds += result.meta.scan_duration_seconds
        upload_duration_seconds += result.meta.receive_duration_seconds

        if result.malware_status is clamav.client.MalwareStatus.OK:
            continue
        elif result.malware_status is clamav.client.MalwareStatus.UNKNOWN:
            raise ValueError('state cannot be unknown if scan succeeded')
        elif result.malware_status is clamav.client.MalwareStatus.FOUND_MALWARE:
            findings.append(result)
        else:
            raise NotImplementedError(result.malware_status)

    if count == 0:
        raise ValueError('results-iterator did not contain any elements')

    if succeeded:
        if len(findings) < 1:
            malware_status = clamav.client.MalwareStatus.OK
        else:
            malware_status = clamav.client.MalwareStatus.FOUND_MALWARE
    else:
        malware_status = clamav.client.MalwareStatus.UNKNOWN

    return ImageScanResult(
        image_reference=image_reference,
        name=name,
        malware_status=malware_status,
        findings=findings,
        scan_count=count,
        scanned_octets=scanned_octets,
        scan_duration_seconds=scan_duration_seconds,
        upload_duration_seconds=upload_duration_seconds,
    )


def scan_oci_image(
    image_reference: typing.Union[str, oci.model.OciImageReference],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
) -> typing.Generator[clamav.client.ScanResult, None, None]:
    manifest = oci_client.manifest(image_reference=image_reference)

    scan_func = functools.partial(
        scan_oci_blob,
        image_reference=image_reference,
        oci_client=oci_client,
        clamav_client=clamav_client,
    )

    if len(manifest.layers) > 1:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(manifest.layers))

        for res in executor.map(scan_func, manifest.layers):
            yield from res
    else:
        yield from scan_func(blob_reference=manifest.layers[0])


def scan_oci_blob(
    blob_reference: oci.model.OciBlobRef,
    image_reference: typing.Union[str, oci.model.OciImageReference],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
) -> typing.Generator[clamav.client.ScanResult, None, None]:
    try:
        yield from scan_oci_blob_filewise(
            blob_reference=blob_reference,
            image_reference=image_reference,
            oci_client=oci_client,
            clamav_client=clamav_client,
        )
    except tarfile.TarError as te:
        logger.warning(f'{image_reference=} {te=} - falling back to layerwise scan')

        yield from scan_oci_blob_layerwise(
            blob_reference=blob_reference,
            image_reference=image_reference,
            oci_client=oci_client,
            clamav_client=clamav_client,
        )


def scan_oci_blob_filewise(
    blob_reference: oci.model.OciBlobRef,
    image_reference: typing.Union[str, oci.model.OciImageReference],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
    chunk_size=8096,
) -> typing.Generator[clamav.client.ScanResult, None, None]:
    blob = oci_client.blob(
        image_reference=image_reference,
        digest=blob_reference.digest,
    )

    # unfortunately, we need a backing tempfile, because we need a seekable filelike-obj for retry
    with tempfile.TemporaryFile() as tmpfh:
        for chunk in blob.iter_content(chunk_size=chunk_size):
            tmpfh.write(chunk)

        tmpfh.seek(0)

        with tarfile.open(
            fileobj=tmpfh,
            mode='r',
        ) as tf:
            for tar_info in tf:
                if not tar_info.isfile():
                    continue
                data = tf.extractfile(member=tar_info)

                scan_result = clamav_client.scan(
                    data=data,
                    name=f'{image_reference}:{blob_reference.digest}:{tar_info.name}',
                )
                yield scan_result


def scan_oci_blob_layerwise(
    blob_reference: oci.model.OciBlobRef,
    image_reference: typing.Union[str, oci.model.OciImageReference],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
) -> typing.Generator[clamav.client.ScanResult, None, None]:
    blob = oci_client.blob(
        image_reference=image_reference,
        digest=blob_reference.digest,
    )

    scan_result = clamav_client.scan(
        data=blob.iter_content(chunk_size=tarfile.RECORDSIZE),
        name=f'{image_reference}:{blob_reference.digest}',
    )
    yield scan_result
