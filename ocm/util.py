import urllib.parse

import ocm


def as_component(
    component: ocm.Component | ocm.ComponentDescriptor,
    /,
) -> ocm.Component:
    if isinstance(component, ocm.Component):
        return component
    if isinstance(component, ocm.ComponentDescriptor):
        return component.component

    raise ValueError(component)


def main_source(
    component: ocm.Component | ocm.ComponentDescriptor,
    *,
    no_source_ok: bool=True,
    ambiguous_ok: bool=True,
) -> ocm.Source | None:
    '''
    returns the "main source" of the given OCM Component. Typically, components will have exactly
    one source, in which the applied logic is to return the sole source-artefact.

    For other cases, behaviour can be controlled via kw-(only-)params:

    no_source_ok: if component has _no_ sources, return None
    ambiguous_ok: if component has more than one source, return first

    In cases where no main-source can be determined, raises ValueError.
    '''
    component = as_component(component)

    if len(component.sources) == 1:
        return component.sources[0]
    elif not component.sources:
        if no_source_ok:
            return None
        else:
            raise ValueError('no sources', component)

    if ambiguous_ok:
        return component.sources[0]

    raise ValueError('could not umambiguously determine main-source', component)


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
