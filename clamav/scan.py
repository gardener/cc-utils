import concurrent.futures
import functools
import logging
import tarfile
import tempfile
import typing

import gci.componentmodel as cm

import ci.log
import clamav.client
import clamav.model
import clamav.util
import oci.client
import oci.model


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


def aggregate_scan_result(
    resource: cm.Resource,
    results: typing.Iterable[clamav.model.ScanResult],
    clamav_version_info: clamav.model.ClamAVVersionInfo,
    name: str=None,
) -> clamav.model.AggregatedScanResult:
    count = 0
    succeeded = True
    scanned_octets = 0
    scan_duration_seconds = 0
    upload_duration_seconds = 0
    findings = []

    for result in results:
        count += 1
        if result.status is clamav.model.ScanStatus.SCAN_FAILED:
            succeeded = False
            continue

        scanned_octets += result.meta.scanned_octets
        scan_duration_seconds += result.meta.scan_duration_seconds
        upload_duration_seconds += result.meta.receive_duration_seconds

        if result.malware_status is clamav.model.MalwareStatus.OK:
            continue
        elif result.malware_status is clamav.model.MalwareStatus.UNKNOWN:
            raise ValueError('state cannot be unknown if scan succeeded')
        elif result.malware_status is clamav.model.MalwareStatus.FOUND_MALWARE:
            findings.append(result)
        else:
            raise NotImplementedError(result.malware_status)

    if count == 0:
        raise ValueError('results-iterator did not contain any elements')

    if succeeded:
        if len(findings) < 1:
            malware_status = clamav.model.MalwareStatus.OK
        else:
            malware_status = clamav.model.MalwareStatus.FOUND_MALWARE
    else:
        malware_status = clamav.model.MalwareStatus.UNKNOWN

    return clamav.model.AggregatedScanResult(
        resource_url=clamav.util.resource_url_from_resource_access(resource.access),
        name=name,
        malware_status=malware_status,
        findings=findings,
        scan_count=count,
        scanned_octets=scanned_octets,
        scan_duration_seconds=scan_duration_seconds,
        upload_duration_seconds=upload_duration_seconds,
        clamav_version_info=clamav_version_info,
    )


def scan_tarfile(
    clamav_client: clamav.client.ClamAVClient,
    tf: tarfile.TarFile,
) -> typing.Generator[clamav.model.ScanResult, None, None]:
    for tar_info in tf:
        if not tar_info.isfile():
            continue
        data = tf.extractfile(member=tar_info)

        with tempfile.TemporaryFile() as tmp_file:
            tmp_file.write(data.read())
            tmp_file.seek(0)

            scan_result = clamav_client.scan(
                data=tmp_file,
                name=f'{tar_info.name}',
            )
            yield scan_result


def _iter_layers(
    image_reference: typing.Union[str, oci.model.OciImageReference],
    oci_client: oci.client.Client,
) -> typing.Generator[oci.model.OciBlobRef, None, None]:
    '''
    yields the flattened layer-blob-references from the given image

    in case of regular ("single") oci-images, this will be said image-manifest's layers.
    in case of an image-list (aka multi-arch), referenced sub-manifests are resolved
    recursively
    '''
    manifest = oci_client.manifest(
        image_reference=image_reference,
        accept=oci.model.MimeTypes.prefer_multiarch,
    )
    if isinstance(manifest, oci.model.OciImageManifest):
        yield from manifest.layers
        return

    if not isinstance(manifest, oci.model.OciImageManifestList):
        raise NotImplementedError(manifest)

    manifest: oci.model.OciImageManifestList
    image_reference = oci.model.OciImageReference.to_image_ref(image_reference)

    for manifest in manifest.manifests:
        sub_manifest_img_ref = f'{image_reference.ref_without_tag}@{manifest.digest}'

        # recurse into (potentially) nested sub-images (typically there should be no nesting)
        yield from _iter_layers(
            image_reference=sub_manifest_img_ref,
            oci_client=oci_client,
        )


def scan_oci_image(
    image_reference: typing.Union[str, oci.model.OciImageReference],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
) -> typing.Generator[clamav.model.ScanResult, None, None]:
    layer_blobs = tuple(_iter_layers(image_reference=image_reference, oci_client=oci_client))

    scan_func = functools.partial(
        scan_oci_blob,
        image_reference=image_reference,
        oci_client=oci_client,
        clamav_client=clamav_client,
    )

    if len(layer_blobs) > 1:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(layer_blobs))

        for res in executor.map(scan_func, layer_blobs):
            yield from res
    else:
        yield from scan_func(blob_reference=layer_blobs[0])


def scan_oci_blob(
    blob_reference: oci.model.OciBlobRef,
    image_reference: typing.Union[str, oci.model.OciImageReference],
    oci_client: oci.client.Client,
    clamav_client: clamav.client.ClamAVClient,
) -> typing.Generator[clamav.model.ScanResult, None, None]:
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
) -> typing.Generator[clamav.model.ScanResult, None, None]:
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
) -> typing.Generator[clamav.model.ScanResult, None, None]:
    blob = oci_client.blob(
        image_reference=image_reference,
        digest=blob_reference.digest,
    )

    scan_result = clamav_client.scan(
        data=blob.iter_content(chunk_size=tarfile.RECORDSIZE),
        name=f'{image_reference}:{blob_reference.digest}',
    )
    yield scan_result
