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
    ctx_repo_url: str,
    delivery_client: delivery.client.DeliveryServiceClient=None,
    cache_dir: str=None,
    validation_mode: cm.ValidationMode=cm.ValidationMode.NONE,
) -> cm.ComponentDescriptor:
    '''
    retrieves the requested, deserialised component-descriptor, preferring delivery-service,
    with a fallback to the underlying oci-registry
    '''
    return _component_descriptor(
        name=name,
        version=version,
        ctx_repo_url=ctx_repo_url,
        delivery_client=delivery_client,
        cache_dir=cache_dir,
        validation_mode=validation_mode,
    )


def components(
    component: typing.Union[cm.ComponentDescriptor, cm.Component],
    delivery_client: delivery.client.DeliveryServiceClient=None,
    cache_dir: str=None,
    validation_mode: cm.ValidationMode=cm.ValidationMode.NONE,
    _visited_component_versions: typing.Tuple[str, str]=(),
):
    component = cnudie.util.to_component(component)

    yield component

    new_visited_component_versions = _visited_component_versions + \
        (component.name, component.version) + \
        tuple((cref.componentName, cref.version) for cref in component.componentReferences)

    for component_ref in component.componentReferences:
        cref_version = (component_ref.componentName, component_ref.version)
        if cref_version in _visited_component_versions:
            continue

        resolved_component = component_descriptor(
            name=component_ref.componentName,
            version=component_ref.version,
            ctx_repo_url=component.current_repository_ctx().baseUrl,
            delivery_client=delivery_client,
            cache_dir=cache_dir,
            validation_mode=validation_mode,
        )

        yield from components(
            component=resolved_component,
            delivery_client=delivery_client,
            cache_dir=cache_dir,
            _visited_component_versions=new_visited_component_versions,
        )


@functools.lru_cache(maxsize=2048)
def _component_descriptor(
    name: str,
    version: str,
    ctx_repo_url: str,
    delivery_client: delivery.client.DeliveryServiceClient=None,
    cache_dir=None,
    validation_mode=cm.ValidationMode.NONE,
):
    if not delivery_client:
        delivery_client = ccc.delivery.default_client_if_available()

    try:
        return delivery_client.component_descriptor(
            name=name,
            version=version,
            ctx_repo_url=ctx_repo_url,
        )
    except:
        pass

    # fallback to resolving from oci-registry
    if delivery_client:
        logger.warning(f'{name=} {version=} {ctx_repo_url=} - falling back to oci-registry')

    return product.v2.download_component_descriptor_v2(
        component_name=name,
        component_version=version,
        ctx_repo_base_url=ctx_repo_url,
        cache_dir=cache_dir,
        validation_mode=validation_mode,
    )
