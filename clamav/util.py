import io
import tarfile
import typing
import logging

import requests.exceptions

import ccc.oci
import gci.componentmodel
import oci.client as oc
import product
import saf.model


logger = logging.getLogger(__name__)


class _FilelikeProxy(io.BytesIO):
    '''
    a fake filelike-object that will mimic the required behaviour (read) "good enough" for
    usage w/ tarfile.open (in stream-mode)
    '''
    def __init__(self, generator, size):
        self.generator = generator
        self.size = size

    def read(self, size: int=-1):
        try:
            return next(self.generator)
        except StopIteration:
            return b''


def iter_image_files(
    container_image_reference: str,
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

    manifest = oci_client.manifest(image_reference=container_image_reference)

    # we ignore cfg-blob (which would be included in manifest.blobs())
    for layer_blob in manifest.layers:
        blob_resp = oci_client.blob(
            image_reference=container_image_reference,
            digest=layer_blob.digest,
            stream=True,
        )

        fileobj = _FilelikeProxy(
            generator=blob_resp.iter_content(
                chunk_size=tarfile.RECORDSIZE,
                decode_unicode=False,
            ),
            size=layer_blob.size,
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


def virus_scan_images(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
    filter_function,
    clamav_client,
) -> typing.Generator[saf.model.MalwarescanResult, None, None]:
    '''Scans components of the given Component Descriptor using ClamAV

    Used by image-scan-trait
    '''
    for component, resource in product.v2.enumerate_oci_resources(
            component_descriptor=component_descriptor_v2,
    ):
        if not filter_function(component, resource):
            continue

        try:
            clamav_findings = list(
                clamav_client.scan_container_image(
                    image_reference=resource.access.imageReference
                )
            )
            yield saf.model.MalwarescanResult(
                    resource=resource,
                    scan_state=saf.model.MalwareScanState.FINISHED_SUCCESSFULLY,
                    findings=[
                        f'{path.split(":")[1]}: {scan_result.virus_signature()}'
                        for scan_result, path in clamav_findings
                    ],
                )
        except requests.exceptions.RequestException as e:
            # log warning and include it as finding to document it via the generated report-mails
            warning = (
                'A connection error occurred while scanning the image '
                f'"{resource.access.imageReference} for viruses: {e}'
            )
            logger.warning(warning)
            yield saf.model.MalwarescanResult(
                    resource=resource,
                    scan_state=saf.model.MalwareScanState.FINISHED_WITH_ERRORS,
                    findings=[warning],
                )
