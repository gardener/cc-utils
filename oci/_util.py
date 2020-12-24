import contextlib
import functools
import httplib2
import logging
import os
import tarfile
import tempfile
import typing

from containerregistry.client import docker_creds
from containerregistry.client import docker_name
from containerregistry.client.v2 import docker_image as v2_image
from containerregistry.client.v2_2 import docker_http
from containerregistry.client.v2_2 import docker_image as v2_2_image
from containerregistry.client.v2_2 import docker_image_list as image_list
from containerregistry.client.v2_2 import docker_session
from containerregistry.client.v2_2 import save
from containerregistry.client.v2_2 import v2_compat
from containerregistry.transport import retry
from containerregistry.transport import transport_pool

import oci.auth as oa
import oci.model as om
import oci.util as ou

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
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
    action: str=docker_http.PULL,
    privileges: oa.Privileges=oa.Privileges.READONLY,
):
    if isinstance(image_name, str):
        image_name = ou.normalise_image_reference(image_name)
    credentials = _mk_credentials(
        image_reference=str(image_name),
        credentials_lookup=credentials_lookup,
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


def _tag_or_digest_reference(image_reference):
  if isinstance(image_reference, str):
    image_reference = docker_name.from_string(image_reference)
  ref_type = type(image_reference)
  if ref_type in (docker_name.Tag, docker_name.Digest):
    return True
  raise ValueError(f'{image_reference=} is does not contain a symbolic or hash tag')


_PROCESSOR_ARCHITECTURE = 'amd64'
_OPERATING_SYSTEM = 'linux'


@contextlib.contextmanager
def pulled_image(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
):
  _tag_or_digest_reference(image_reference)

  transport = _mk_transport_pool()
  image_reference = ou.normalise_image_reference(image_reference)
  image_reference = docker_name.from_string(image_reference)
  creds = _mk_credentials(
      image_reference=image_reference,
      credentials_lookup=credentials_lookup,
  )

  # OCI Image Manifest is compatible with Docker Image Manifest Version 2,
  # Schema 2. We indicate support for both formats by passing both media types
  # as 'Accept' headers.
  #
  # For reference:
  #   OCI: https://github.com/opencontainers/image-spec
  #   Docker: https://docs.docker.com/registry/spec/manifest-v2-2/
  accept = docker_http.SUPPORTED_MANIFEST_MIMES

  try:
    logger.info(f'Pulling v2.2 image from {image_reference}..')
    with v2_2_image.FromRegistry(image_reference, creds, transport, accept) as v2_2_img:
      if v2_2_img.exists():
        yield v2_2_img
        return

    # XXX TODO: use streaming rather than writing to local FS
    # if outfile is given, we must use it instead of an ano
    logger.debug(f'Pulling manifest list from {image_reference}..')
    with image_list.FromRegistry(image_reference, creds, transport) as img_list:
      if img_list.exists():
        platform = image_list.Platform({
            'architecture': _PROCESSOR_ARCHITECTURE,
            'os': _OPERATING_SYSTEM,
        })
        # pytype: disable=wrong-arg-types
        with img_list.resolve(platform) as default_child:
          yield default_child
          return

    logger.info(f'Pulling v2 image from {image_reference}..')
    with v2_image.FromRegistry(image_reference, creds, transport) as v2_img:
      if v2_img.exists():
        with v2_compat.V22FromV2(v2_img) as v2_2_img:
          yield v2_2_img
          return

    raise om.OciImageNotFoundException(f'failed to retrieve {image_reference=} - does it exist?')

  except Exception as e:
    raise e


def _push_image(
    image_reference: str,
    image_file: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
    threads=8
):
    if not image_reference:
        raise ValueError(image_reference)
    if not os.path.isfile(image_file):
        raise ValueError(f'not an exiting file: {image_file=}')

    transport = _mk_transport_pool()

    image_reference = ou.normalise_image_reference(image_reference)
    image_reference = docker_name.from_string(image_reference)

    creds = _mk_credentials(
        image_reference=image_reference,
        credentials_lookup=credentials_lookup,
        privileges=oa.Privileges.READWRITE,
    )
    # XXX fail if no creds were found

    with v2_2_image.FromTarball(image_file) as v2_2_img:
      try:
        with docker_session.Push(
            image_reference,
            creds,
            transport,
            threads=threads,
        ) as session:
          session.upload(v2_2_img)
          digest = v2_2_img.digest()
          logger.info(f'{image_reference} was uploaded - digest: {digest}')
      except Exception as e:
        import traceback
        traceback.print_exc()
        raise e


def _put_raw_image_manifest(
    image_reference: str,
    raw_contents: bytes,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
):
    image_name = docker_name.from_string(image_reference)

    push_sess = docker_session.Push(
        name=image_name,
        creds=_mk_credentials(
            image_reference=image_reference,
            credentials_lookup=credentials_lookup,
            privileges=oa.Privileges.READWRITE,
        ),
        transport=_mk_transport_pool(),
    )

    class ImageMock:
        def digest(self):
            return image_name.tag

        def manifest(self):
            return raw_contents

        def media_type(self):
            return docker_http.MANIFEST_SCHEMA2_MIME

    image_mock = ImageMock()

    push_sess._put_manifest(image=image_mock, use_digest=True)


def _pull_image(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
    outfileobj=None,
):
  if not image_reference:
      raise ValueError(image_reference)

  image_reference = ou.normalise_image_reference(image_reference=image_reference)

  outfileobj = outfileobj if outfileobj else tempfile.TemporaryFile()

  with tarfile.open(fileobj=outfileobj, mode='w:') as tar:
    with pulled_image(
        image_reference=image_reference,
        credentials_lookup=credentials_lookup,
    ) as image:
      image_reference = docker_name.from_string(image_reference)
      save.tarball(_make_tag_if_digest(image_reference), image, tar)
      return outfileobj


_DEFAULT_TAG = 'i-was-a-digest'


def _make_tag_if_digest(name):
  if isinstance(name, docker_name.Tag):
    return name
  return docker_name.Tag('{repo}:{tag}'.format(
      repo=str(name.as_repository()), tag=_DEFAULT_TAG))
