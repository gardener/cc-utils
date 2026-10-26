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


def normalise_component_name(component_name: str) -> str:
    return component_name.lower()  # oci-spec demands lowercase


def to_component_id_and_repository_url(
    component: ocm.Component | ocm.ComponentDescriptor | ocm.ComponentIdentity | str,
    repository: ocm.OciOcmRepository | str=None,
) -> tuple[ocm.Component | ocm.ComponentIdentity, str]:
    if isinstance(component, str):
        name, version = component.rsplit(':', 1)
        component = ocm.ComponentIdentity(
            name=name,
            version=version,
        )

    if isinstance(component, ocm.ComponentDescriptor):
        component = component.component
    elif isinstance(component, ocm.ComponentIdentity) and not repository:
        raise ValueError('repository must be passed if calling w/ component-identity')

    if not repository:  # component is sure to be of type ocm.Component by now (checked above)
        repository = component.current_ocm_repo

    if isinstance(repository, ocm.OciOcmRepository):
        repo_base_url = repository.baseUrl
    elif isinstance(repository, str):
        repo_base_url = repository
    else:
        raise ValueError(f'only OciOcmRepository is supported - got: {repository=}')

    return component, repo_base_url


def oci_ref(
    component: ocm.Component | ocm.ComponentDescriptor | ocm.ComponentIdentity | str,
    repository: ocm.OciOcmRepository | str=None,
) -> oci.model.OciImageReference:
    component, repo_base_url = to_component_id_and_repository_url(
        component=component,
        repository=repository,
    )

    return oci.model.OciImageReference(
        '/'.join((
            repo_base_url.rstrip('/'),
            'component-descriptors',
            f'{component.name.lower()}:{component.version}',
        )),
    )


def target_oci_ref(
    component: ocm.Component,
    component_ref: ocm.ComponentReference=None,
    component_version: str=None,
) -> str:
    if not component_ref:
        component_ref = component
        component_name = component_ref.name
    else:
        component_name = component_ref.componentName

    component_name = normalise_component_name(component_name)
    component_version = component_ref.version

    last_ocm_repo = component.current_ocm_repo

    return last_ocm_repo.component_version_oci_ref(
        name=component_name,
        version=component_version,
    )


def oci_artefact_reference(
    component: (
        ocm.Component
        | ocm.ComponentIdentity
        | ocm.ComponentReference
        | str  # 'name:version'
        | tuple[str, str]  # (name, version)
    ),
    ocm_repository: str | ocm.OciOcmRepository=None,
) -> str:
    if isinstance(component, ocm.Component):
        if not ocm_repository:
            ocm_repository = component.current_ocm_repo
        component_name = component.name
        component_version = component.version
    elif isinstance(component, ocm.ComponentIdentity):
        component_name = component.name
        component_version = component.version
    elif isinstance(component, ocm.ComponentReference):
        component_name = component.componentName
        component_version = component.version
    elif isinstance(component, str):
        component_name, component_version = component.split(':')
    elif isinstance(component, tuple):
        if not len(component) == 2 or not all(isinstance(x, str) for x in component):
            raise TypeError('if a tuple is given as component, it must contain two strings')
        component_name, component_version = component
    else:
        raise ValueError(component)

    if not ocm_repository:
        raise ValueError('ocm_repository must be given unless a Component is passed.')
    if isinstance(ocm_repository, str):
        ocm_repository = ocm.OciOcmRepository(baseUrl=ocm_repository)
    elif not isinstance(ocm_repository, ocm.OciOcmRepository):
        raise TypeError(type(ocm_repository))

    return ocm_repository.component_version_oci_ref(
        name=component_name,
        version=component_version,
    )
