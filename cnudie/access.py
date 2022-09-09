import gci.componentmodel as cm

import ioutil


def s3_access_as_blob_descriptor(
    s3_client: 'botocore.client.S3',
    s3_access: cm.S3Access,
    chunk_size: int=4096,
    name: str=None,
) -> ioutil.BlobDescriptor:
    blob = s3_client.get_object(Bucket=s3_access.bucketName, Key=s3_access.objectKey)

    size = blob['ContentLength']
    body = blob['Body']

    return ioutil.BlobDescriptor(
        content=body.iter_chunks(chunk_size=chunk_size),
        size=size,
        name=name or f's3://{s3_access.bucketName}/{s3_access.objectKey}',
    )
