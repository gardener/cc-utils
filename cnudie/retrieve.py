import functools
import logging
import typing

import gci.componentmodel as cm

import ccc.delivery
import cnudie.util
import delivery.client
import product.v2

logger = logging.getLogger(__name__)


def component_descriptor(
    name: str,
    version: str,
    ctx_repo_url: str=None,
    ctx_repo: cm.RepositoryContext=None,
    delivery_client: delivery.client.DeliveryServiceClient=None,
    cache_dir: str=None,
    validation_mode: cm.ValidationMode=cm.ValidationMode.NONE,
) -> cm.ComponentDescriptor:
    '''
    retrieves the requested, deserialised component-descriptor, preferring delivery-service,
    with a fallback to the underlying oci-registry
    '''
    if not (bool(ctx_repo_url) ^ bool(ctx_repo)):
        raise ValueError('exactly one of ctx_repo, ctx_repo_url must be passed')

    if ctx_repo_url:
        logger.warning('passing ctx_repo_url is deprecated - pass ctx_repo')
        ctx_repo = cm.OciRepositoryContext(
            baseUrl=ctx_repo_url,
            componentNameMapping=cm.OciComponentNameMapping.URL_PATH,
        )

    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    ctx_repo: cm.OciRepositoryContext

    return _component_descriptor(
        name=name,
        version=version,
        ctx_repo=ctx_repo,
        delivery_client=delivery_client,
        cache_dir=cache_dir,
        validation_mode=validation_mode,
    )


def components(
    component: typing.Union[cm.ComponentDescriptor, cm.Component],
    cache_dir: str=None,
    delivery_client: delivery.client.DeliveryServiceClient=None,
    validation_mode: cm.ValidationMode=cm.ValidationMode.NONE,
):
    if isinstance(component, cm.ComponentDescriptor):
        component = component.component
    elif isinstance(component, cm.Component):
        component = component
    else:
        raise TypeError(component)

    _visited_component_versions = [
        (component.name, component.version)
    ]

    def resolve_component_dependencies(
        component: cm.Component,
    ) -> typing.Generator[cm.Component, None, None]:
        nonlocal cache_dir
        nonlocal delivery_client
        nonlocal validation_mode
        nonlocal _visited_component_versions

        yield component

        for component_ref in component.componentReferences:
            cref = (component_ref.componentName, component_ref.version)

            if cref in _visited_component_versions:
                continue
            else:
                _visited_component_versions.append(cref)

            resolved_component = component_descriptor(
                name=component_ref.componentName,
                version=component_ref.version,
                ctx_repo=component.current_repository_ctx(),
                delivery_client=delivery_client,
                cache_dir=cache_dir,
                validation_mode=validation_mode,
            )

            yield from resolve_component_dependencies(
                component=resolved_component.component,
            )

    yield from resolve_component_dependencies(
        component=component,
    )


def component_diff(
    left_component: typing.Union[cm.Component, cm.ComponentDescriptor],
    right_component: typing.Union[cm.Component, cm.ComponentDescriptor],
    ignore_component_names=(),
    delivery_client: delivery.client.DeliveryServiceClient=None,
    cache_dir: str=None,
):
    left_component = cnudie.util.to_component(left_component)
    right_component = cnudie.util.to_component(right_component)

    left_components = tuple(
        c for c in
        components(
            component=left_component,
            delivery_client=delivery_client,
            cache_dir=cache_dir,
        )
        if c.name not in ignore_component_names
    )
    right_components = tuple(
        c for c in
        components(
            component=right_component,
            delivery_client=delivery_client,
            cache_dir=cache_dir,
        )
        if c.name not in ignore_component_names
    )

    return cnudie.util.diff_components(
        left_components=left_components,
        right_components=right_components,
        ignore_component_names=ignore_component_names,
    )


@functools.lru_cache(maxsize=2048)
def _component_descriptor(
    name: str,
    version: str,
    ctx_repo: cm.RepositoryContext,
    delivery_client: delivery.client.DeliveryServiceClient=None,
    cache_dir=None,
    validation_mode=cm.ValidationMode.NONE,
):
    if not delivery_client:
        delivery_client = ccc.delivery.default_client_if_available()

    if not isinstance(ctx_repo, cm.OciRepositoryContext):
        raise NotImplementedError(ctx_repo)

    ctx_repo: cm.OciRepositoryContext

    ctx_repo_url = ctx_repo.baseUrl

    # delivery-client may still be None
    if delivery_client:
        try:
            return delivery_client.component_descriptor(
                name=name,
                version=version,
                ctx_repo_url=ctx_repo_url,
            )
        except:
            import traceback
            traceback.print_exc()

    # fallback to resolving from oci-registry
    if delivery_client:
        logger.warning(f'{name=} {version=} {ctx_repo_url=} - falling back to oci-registry')

    return product.v2.download_component_descriptor_v2(
        component_name=name,
        component_version=version,
        ctx_repo=ctx_repo,
        cache_dir=cache_dir,
        validation_mode=validation_mode,
    )
