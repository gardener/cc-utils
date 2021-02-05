import io
import tarfile
import typing

import oci.client as oc

import ccc.oci


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
