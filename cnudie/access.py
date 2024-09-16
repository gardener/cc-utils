import collections.abc

import cnudie.access
import gci.componentmodel as cm
import ioutil
import oci.client
import tarutil


def s3_access_as_blob_descriptor(
    s3_client: 'botocore.client.S3',
    s3_access: cm.S3Access,
    chunk_size: int=4096,
    name: str=None,
) -> ioutil.BlobDescriptor:
    if not s3_client:
        raise ValueError('must pass-in s3-client')

    blob = s3_client.get_object(Bucket=s3_access.bucketName, Key=s3_access.objectKey)

    size = blob['ContentLength']
    body = blob['Body']

    return ioutil.BlobDescriptor(
        content=body.iter_chunks(chunk_size=chunk_size),
        size=size,
        name=name or f's3://{s3_access.bucketName}/{s3_access.objectKey}',
    )


def create_resolve_access(
    oci_client: oci.client.Client=None,
    s3_client: 'botocore.client.S3'=None,
    image_reference: str=None,
) -> collections.abc.Callable[[cm.Access], collections.abc.Generator[bytes, None, None]]:
    def resolve(
        access: cm.Access,
    ):
        if access.type is cm.AccessType.OCI_REGISTRY:
            if not oci_client:
                raise ValueError('`oci_client` must not be empty')

            return _resolve_oci(
                access=access,
                oci_client=oci_client,
            )

        elif access.type is cm.AccessType.S3:
            if not s3_client:
                raise ValueError('`s3_client` must not be empty')

            return _resolve_s3(
                access=access,
                s3_client=s3_client,
            )

        elif access.type is cm.AccessType.LOCAL_BLOB:
            return _resolve_local_blob(
                access=access,
                image_reference=image_reference,
            )

    def _resolve_s3(
        access: cm.AccessType.S3,
        s3_client: 'botocore.client.S3',
    ) -> collections.abc.Generator[bytes, None, None]:
        return tarutil.concat_blobs_as_tarstream(
            blobs=[
                cnudie.access.s3_access_as_blob_descriptor(
                    s3_client=s3_client,
                    s3_access=access,
                ),
            ]
        )

    def _resolve_oci(
        access: cm.AccessType.OCI_REGISTRY,
        oci_client: oci.client.Client,
    ) -> collections.abc.Generator[bytes, None, None]:
        return oci.image_layers_as_tarfile_generator(
            image_reference=access.imageReference,
            oci_client=oci_client,
            include_config_blob=False,
            fallback_to_first_subimage_if_index=True
        )

    def _resolve_local_blob(
        access: cm.AccessType.LOCAL_BLOB,
        image_reference: str=None,
    ) -> collections.abc.Generator[bytes, None, None]:
        if access.globalAccess:
            image_reference = access.globalAccess.ref
            digest = access.globalAccess.digest
            size = access.globalAccess.size

        else:
            if not image_reference:
                raise ValueError('`image_reference` must not be empty to resolve local blob')

            digest = access.localReference.lower()
            size = access.size

        blob = oci_client.blob(
            image_reference=image_reference,
            digest=digest,
            stream=True,
        )

        return tarutil.concat_blobs_as_tarstream(
            blobs=[
                ioutil.BlobDescriptor(
                    content=blob.iter_content(chunk_size=4096),
                    size=size,
                    name=access.referenceName,
                )
            ],
        )

    return resolve
