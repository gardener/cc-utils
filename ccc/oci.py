import logging
import functools
import traceback

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
            if absent_ok:
                return None # fallback to docker-cfg (or try w/o auth)
            else:
                raise RuntimeError(
                f'No credentials found for {image_reference} with {privileges=}'
            )
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

    add_oci_request_logging_handler()

    return oc.Client(
        credentials_lookup=credentials_lookup,
        routes=routes,
    )


def add_oci_request_logging_handler():
    import ccc.elasticsearch
    if es_client := ccc.elasticsearch.default_client_if_available():
        logger = logging.getLogger('oci.client.request_logger')
        handler = _OciRequestHandler(level=logging.DEBUG, es_client=es_client)
        logger.addHandler(handler)


class _OciRequestHandler(logging.Handler):
    def __init__(
        self,
        level,
        es_client,
        *args,
        **kwargs,
    ) -> None:
        self.es_client = es_client
        super().__init__(level=level, *args, **kwargs)

    def emit(self, record: logging.LogRecord) -> None:
        method = record.__dict__.get('method')
        url = record.__dict__.get('url')
        try:
            self.es_client.store_document(
                index='component_descriptor_pull',
                body={
                    'method': method,
                    'url': url,
                    'stacktrace': traceback.format_stack(),
                }
            )
        except:
            logger.warning(traceback.format_exc())
            logger.warning('could not sent oci request log to elastic search')
