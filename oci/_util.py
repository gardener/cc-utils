import functools
import httplib2
import logging
import typing

from containerregistry.client import docker_creds
from containerregistry.client import docker_name
from containerregistry.client.v2_2 import docker_http
from containerregistry.transport import retry
from containerregistry.transport import transport_pool

import oci.auth as oa

logger = logging.getLogger(__name__)

'''
all symbols defined here are not intended to be used outside the containing package
'''

# type-alias for typehints
image_reference = str


@functools.lru_cache()
def _credentials(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges], oa.OciConfig],
    privileges: oa.Privileges=None,
    absent_ok: bool=False,
):
    if not isinstance(image_reference, str):
        raise ValueError(image_reference)

    oci_creds = credentials_lookup(
        image_reference=image_reference,
        privileges=privileges,
        absent_ok=absent_ok,
    )

    if not oci_creds:
        return None

    # XXX currently, we only support basic-auth
    return docker_creds.Basic(username=oci_creds.username, password=oci_creds.password)


def _mk_credentials(
    image_reference,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges], oa.OciConfig],
    privileges: oa.Privileges=None
):
  if isinstance(image_reference, str):
    image_reference = docker_name.from_string(name=image_reference)
  try:
    # first try container_registry cfgs from available cfg
    creds = _credentials(
        image_reference=str(image_reference),
        credentials_lookup=credentials_lookup,
        privileges=privileges,
        absent_ok=True,
    )
    if not creds:
      logger.warning(f'could not find rw-creds for {image_reference}')
      # fall-back to default docker lookup
      creds = docker_creds.DefaultKeychain.Resolve(image_reference)

    return creds
  except Exception as e:
    logger.warning(f'Error resolving credentials for {image_reference}: {e}')
    raise e


def _mk_transport(
    image_name: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges], oa.OciConfig],
    action: str=docker_http.PULL,
    privileges: oa.Privileges=oa.Privileges.READONLY,
):
    if isinstance(image_name, str):
        image_name = normalise_image_reference(image_name)
    credentials = _mk_credentials(
        image_reference=str(image_name),
        privileges=privileges,
    )
    if isinstance(image_name, str):
        image_name = docker_name.from_string(name=image_name)

    transport_pool = _mk_transport_pool(size=1)

    transport = docker_http.Transport(
        name=image_name,
        creds=credentials,
        transport=transport_pool,
        action=docker_http.PULL,
    )

    return transport


def _mk_transport_pool(
    size=8,
    disable_ssl_certificate_validation=False,
):
  # XXX: should cache transport-pools iff image-references refer to same oauth-domain
  # XXX: pass `disable_ssl_certificate_validation`-arg from calling functions
  Http_ctor = functools.partial(
    httplib2.Http,
    disable_ssl_certificate_validation=disable_ssl_certificate_validation
  )
  retry_factory = retry.Factory()
  retry_factory = retry_factory.WithSourceTransportCallable(Http_ctor)
  transport = transport_pool.Http(retry_factory.Build, size=size)
  return transport
