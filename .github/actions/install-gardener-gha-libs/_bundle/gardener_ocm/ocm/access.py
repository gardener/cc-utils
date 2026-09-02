import hashlib
import urllib.parse

import ocm

import ioutil
import oci.client
import oci.model


def s3_access_as_blob_descriptor(
    s3_client: 'botocore.client.S3',
    s3_access: ocm.S3Access | ocm.LegacyS3Access,
    chunk_size: int=8192,
    name: str=None,
) -> ioutil.BlobDescriptor:
    if not s3_client:
        raise ValueError('must pass-in s3-client')

    if isinstance(s3_access, ocm.LegacyS3Access):
        bucket = s3_access.bucketName
        key = s3_access.objectKey
    else:
        bucket = s3_access.bucket
        key = s3_access.key

    blob = s3_client.get_object(Bucket=bucket, Key=key)

    size = blob['ContentLength']
    body = blob['Body']

    return ioutil.BlobDescriptor(
        content=body.iter_chunks(chunk_size=chunk_size),
        size=size,
        name=name or f's3://{bucket}/{key}',
    )


def access_to_digest_lookup(
    access: ocm.Access,
    oci_client: oci.client.Client=None,
    s3_client: 'botocore.client.S3'=None,
    chunk_size: int=8192,
) -> ocm.DigestSpec:
    if access.type is ocm.AccessType.OCI_REGISTRY:
        image_reference = oci.model.OciImageReference(
            image_reference=oci_client.to_digest_hash(
                image_reference=access.imageReference,
                accept=oci.model.MimeTypes.prefer_multiarch,
            )
        )

        digest = image_reference.digest

        return ocm.DigestSpec(
            hashAlgorithm='SHA-256',
            normalisationAlgorithm=ocm.NormalisationAlgorithm.OCI_ARTIFACT_DIGEST,
            value=digest,
        )

    elif access.type is ocm.AccessType.LOCAL_BLOB:
        reference = access.globalAccess.digest if access.globalAccess else access.localReference

        digest = reference.lower().removeprefix('sha256:')

        return ocm.DigestSpec(
            hashAlgorithm='SHA-256',
            normalisationAlgorithm=ocm.NormalisationAlgorithm.GENERIC_BLOB_DIGEST,
            value=digest,
        )

    elif access.type in (
        ocm.AccessType.S3,
        ocm.AccessType.S3_V2,
    ):
        if isinstance(access, ocm.LegacyS3Access):
            bucket = access.bucketName
            key = access.objectKey
        else:
            bucket = access.bucket
            key = access.key

        blob = s3_client.get_object(Bucket=bucket, Key=key)['Body']

        digest = hashlib.sha256()
        for chunk in blob.iter_chunks(chunk_size=chunk_size):
            digest.update(chunk)

        return ocm.DigestSpec(
            hashAlgorithm='SHA-256',
            normalisationAlgorithm=ocm.NormalisationAlgorithm.GENERIC_BLOB_DIGEST,
            value=digest.hexdigest(),
        )

    return ocm.ExcludeFromSignatureDigest()


def artifact_url(
    component: ocm.Component,
    artifact: ocm.Resource | ocm.Source,
) -> str:
    access = artifact.access

    if isinstance(access, ocm.GithubAccess):
        return access.repoUrl

    elif isinstance(access, ocm.LocalBlobAccess):
        image_reference = component.current_ocm_repo.component_oci_ref(component.name)
        return f'{image_reference}@{access.localReference}'

    elif isinstance(access, ocm.OciAccess):
        return access.imageReference

    elif isinstance(access, ocm.RelativeOciAccess):
        return access.reference

    elif isinstance(access, ocm.S3Access):
        return f'http://{access.bucket}.s3.amazonaws.com/{access.key}'

    elif isinstance(access, ocm.LegacyS3Access):
        return f'http://{access.bucketName}.s3.amazonaws.com/{access.objectKey}'

    elif isinstance(access, ocm.LocalBlobGlobalAccess):
        return access.ref

    else:
        raise ValueError(access)


def to_absolute_oci_access(
    access: ocm.OciAccess | ocm.RelativeOciAccess,
    ocm_repo: ocm.OciOcmRepository | None=None,
) -> ocm.OciAccess:
    if access.type is ocm.AccessType.OCI_REGISTRY:
        pass

    elif access.type is ocm.AccessType.RELATIVE_OCI_REFERENCE:
        if not '://' in ocm_repo.baseUrl:
            base_url = urllib.parse.urlparse(f'x://{ocm_repo.baseUrl}').netloc
        else:
            base_url = urllib.parse.urlparse(ocm_repo.baseUrl).netloc

        access = ocm.OciAccess(
            imageReference=f'{base_url.rstrip('/')}/{access.reference.lstrip('/')}',
        )

    else:
        raise ValueError(f'Unsupported access type: {access.type}')

    return access
