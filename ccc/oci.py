import collections.abc
import functools
import logging

import requests

import oci.auth as oa
import oci.client as oc
import oci.model
import model.container_registry


logger = logging.getLogger(__name__)


@functools.lru_cache
def oci_cfg_lookup(
    cfg_factory=None,
) -> collections.abc.Callable[[str, oa.Privileges, bool], oa.OciCredentials]:
    def find_credentials(
        image_reference: str,
        privileges: oa.Privileges=oa.Privileges.READONLY,
        absent_ok: bool=True,
    ):
        registry_cfg = model.container_registry.find_config(
            image_reference=image_reference,
            privileges=privileges,
            cfg_factory=cfg_factory,
        )
        if not registry_cfg:
            if absent_ok:
                return None # fallback to docker-cfg (or try w/o auth)
            else:
                raise RuntimeError(
                f'No credentials found for {image_reference} with {privileges=}'
            )
        creds = registry_cfg.credentials()

        if registry_cfg.registry_type() is oci.model.OciRegistryType.AWS:
            # XXX enhance `container_registry` model to be more flexible
            return oa.OciAccessKeyCredentials(
                access_key_id=creds.username(),
                secret_access_key=creds.passwd(),
            )

        return oa.OciBasicAuthCredentials(
            username=creds.username(),
            password=creds.passwd(),
        )

    return find_credentials


@functools.cache
def oci_client_async(
    credentials_lookup: collections.abc.Callable=None,
    cfg_factory=None,
    http_connection_pool_size: int | None=None,
    tag_preprocessing_callback: collections.abc.Callable[[str], str]=None,
    tag_postprocessing_callback: collections.abc.Callable[[str], str]=None,
) -> 'oci.client_async.Client':
    import aiohttp
    import oci.client_async

    def base_api_lookup(image_reference):
        registry_cfg = model.container_registry.find_config(
            image_reference=image_reference,
            privileges=None,
            cfg_factory=cfg_factory,
        )
        if registry_cfg and (base_url := registry_cfg.api_base_url()):
            return base_url
        return oc.base_api_url(image_reference)

    routes = oc.OciRoutes(base_api_lookup)

    if not credentials_lookup:
        credentials_lookup = oci_cfg_lookup(cfg_factory=cfg_factory)

    if http_connection_pool_size is None: # 0 is a valid value here (meaning no limitation)
        connector = aiohttp.TCPConnector()
    else:
        connector = aiohttp.TCPConnector(
            limit=http_connection_pool_size,
        )

    session = aiohttp.ClientSession(
        connector=connector,
    )

    return oci.client_async.Client(
        credentials_lookup=credentials_lookup,
        routes=routes,
        session=session,
        tag_preprocessing_callback=tag_preprocessing_callback,
        tag_postprocessing_callback=tag_postprocessing_callback,
    )


@functools.cache
def oci_client(
    credentials_lookup: collections.abc.Callable=None,
    cfg_factory=None,
    http_connection_pool_size:int=16,
    tag_preprocessing_callback: collections.abc.Callable[[str], str]=None,
    tag_postprocessing_callback: collections.abc.Callable[[str], str]=None,
) -> oc.Client:
    def base_api_lookup(image_reference):
        registry_cfg = model.container_registry.find_config(
            image_reference=image_reference,
            privileges=None,
            cfg_factory=cfg_factory,
        )
        if registry_cfg and (base_url := registry_cfg.api_base_url()):
            return base_url
        return oc.base_api_url(image_reference)

    routes = oc.OciRoutes(base_api_lookup)

    if not credentials_lookup:
        credentials_lookup = oci_cfg_lookup(cfg_factory=cfg_factory)

    # increase poolsize (defaults: 10) to allow for greater parallelism
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=http_connection_pool_size,
        pool_maxsize=http_connection_pool_size,
    )
    session.mount('https://', adapter)

    return oc.Client(
        credentials_lookup=credentials_lookup,
        routes=routes,
        session=session,
        tag_preprocessing_callback=tag_preprocessing_callback,
        tag_postprocessing_callback=tag_postprocessing_callback,
    )
