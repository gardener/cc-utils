import logging
import functools
import traceback

import ccc.elasticsearch
import oci.auth as oa
import oci.client as oc
import model.container_registry


logger = logging.getLogger(__name__)


@functools.lru_cache
def oci_cfg_lookup():
    def find_credentials(
        image_reference: str,
        privileges: oa.Privileges=oa.Privileges.READONLY,
        absent_ok: bool=True,
    ):
        registry_cfg = model.container_registry.find_config(
            image_reference=image_reference,
            privileges=privileges,
        )
        if not registry_cfg:
            return None # fallback to docker-cfg (or try w/o auth)
        creds = registry_cfg.credentials()
        return oa.OciBasicAuthCredentials(
            username=creds.username(),
            password=creds.passwd(),
        )

    return find_credentials


def oci_client(credentials_lookup=oci_cfg_lookup()):
    def base_api_lookup(image_reference):
        registry_cfg = model.container_registry.find_config(
            image_reference=image_reference,
            privileges=None,
        )
        if registry_cfg and (base_url := registry_cfg.api_base_url()):
            return base_url
        return oc.base_api_url(image_reference)

    routes = oc.OciRoutes(base_api_lookup)

    client = oc.Client(
        credentials_lookup=credentials_lookup,
        routes=routes,
    )

    client_request = client._request

    def wrap_request(
        url: str,
        method: str='GET',
        *args,
        **kwargs,
    ):
        result = None
        try:
            log_to_es(method=method, url=url)
        except:
            logger.warning(traceback.format_exc())
            logger.warning('could not send log info to elastic search')

        try:
            result = client_request(url=url, method=method, *args, **kwargs)
        finally:
            return result

    client._request = wrap_request
    return client


def log_to_es(method, url):
    if es_client := ccc.elasticsearch.default_client_if_available():
        es_client.store_document(
            index='component_descriptor_pull',
            body={
                'method': method,
                'url': url,
                'stacktrace': traceback.format_stack(),
            }
        )
