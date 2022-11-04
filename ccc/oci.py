import functools
import logging
import traceback
import typing

import ci.util
import ccc.concourse
import ccc.elasticsearch
import ctx
import oci.auth as oa
import oci.client as oc
import model.container_registry


logger = logging.getLogger(__name__)


@functools.lru_cache
def oci_cfg_lookup(
    cfg_factory=None,
) -> typing.Callable[[str, oa.Privileges, bool], oa.OciCredentials]:
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
        return oa.OciBasicAuthCredentials(
            username=creds.username(),
            password=creds.passwd(),
        )

    return find_credentials


def oci_request_handler_requirements_fulfilled() -> bool:
    '''
    checks requirements for oci request handler installation
    returns False if a requirement is not fulfilled and prints a warning with reason
    '''
    try:
        cfg_set = ctx.cfg_set()
        cfg_set.elasticsearch()
        return True
    except ValueError:
        logger.warning('no elasticsearch config found')
        return False


@functools.cache
def oci_client(
    credentials_lookup: typing.Callable=None,
    install_logging_handler: bool=True,
    cfg_factory=None,
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

    install_logging_handler &= ci.util._running_on_ci()
    if install_logging_handler:
        try:
            if oci_request_handler_requirements_fulfilled():
                _add_oci_request_logging_handler_unless_already_registered()
            else:
                logger.warning('skipping oci request logger installation')
        except:
            # do not fail just because of logging-issue
            import traceback
            traceback.print_exc()

    if not credentials_lookup:
        credentials_lookup = oci_cfg_lookup(cfg_factory=cfg_factory)

    return oc.Client(
        credentials_lookup=credentials_lookup,
        routes=routes,
    )


class _OciRequestHandler(logging.Handler):
    def __init__(
        self,
        level,
        es_client,
        *args,
        **kwargs,
    ) -> None:
        self.es_client = es_client
        self.shortcut = False
        super().__init__(level=level, *args, **kwargs)

    def emit(self, record: logging.LogRecord) -> None:
        if self.shortcut:
            logger.info('oci-request-reporting to elasticsearch was shortcut due to previous error')
            return

        method = record.__dict__.get('method')
        url = record.__dict__.get('url')
        try:
            self.es_client.store_document(
                index='oci_request',
                body={
                    'method': method,
                    'url': url,
                    'stacktrace': traceback.format_stack(),
                }
            )
        except:
            logger.warning(traceback.format_exc())
            logger.warning('could not send oci request log to elastic search')
            self.shortcut = True


_client_sentinel = object()
_es_client = _client_sentinel # init by first caller
_es_handler = None


def _add_oci_request_logging_handler_unless_already_registered():
    global _es_client
    global _es_handler

    if _es_client is _client_sentinel:
        _es_client = ccc.elasticsearch.default_client_if_available()

    if not _es_client:
        return

    if not _es_handler:
        _es_handler = _OciRequestHandler(level=logging.DEBUG, es_client=_es_client)

    es_logger = logging.getLogger('oci.client.request_logger')
    es_logger.setLevel(logging.DEBUG)

    if not _es_handler in es_logger.handlers:
        es_logger.addHandler(_es_handler)
