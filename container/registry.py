# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""This package pulls images from a Docker Registry."""

import functools
import logging

import oci.util

import ci.util
import oci
import oci.auth as oa
import model.container_registry
from model.container_registry import Privileges

from containerregistry.client import docker_creds
from containerregistry.client import docker_name
from containerregistry.client.v2 import docker_image as v2_image
from containerregistry.client.v2_2 import docker_http
from containerregistry.client.v2_2 import docker_image as v2_2_image
from containerregistry.client.v2_2 import docker_image_list as image_list
from containerregistry.client.v2_2 import docker_session
from containerregistry.transport import retry
from containerregistry.transport import transport_pool

import httplib2

logger = logging.getLogger(__name__)


# keep for backwards-compat (XXX rm eventually)
normalise_image_reference = oci.util.normalise_image_reference


def _convert_privileges(privileges):
    if isinstance(privileges, oa.Privileges):
        if privileges is oa.Privileges.READONLY:
            privileges = model.container_registry.Privileges.READ_ONLY
        elif privileges is oa.Privileges.READWRITE:
            privileges = model.container_registry.Privileges.READ_WRITE
        else:
            raise NotImplementedError

    return privileges


def _mk_credentials_lookup(
    image_reference: str,
    privileges: oa.Privileges=model.container_registry.Privileges.READ_ONLY,
):
    privileges = _convert_privileges(privileges=privileges)

    def find_credentials(image_reference, privileges, absent_ok):
        if privileges:
            privileges = _convert_privileges(privileges)
        registry_cfg = model.container_registry.find_config(
            image_reference,
            privileges,
        )
        if not registry_cfg:
            return None # fallback to docker-cfg
        creds = registry_cfg.credentials()
        return oa.OciBasicAuthCredentials(
            username=creds.username(),
            password=creds.passwd(),
        )

    return find_credentials


def _inject_credentials_lookup(inner_function: callable):
    def outer_function(
        *args,
        image_reference=None,
        image_name=None,
        privileges=model.container_registry.Privileges.READ_ONLY,
        **kwargs
      ):
        if image_reference:
            kwargs['image_reference'] = image_reference
        if image_name:
            kwargs['image_name'] = image_name

        if not image_reference and not image_name and args:
            image_reference = args[0]

        if image_reference and image_name:
            raise ValueError('image_reference and image_name must not both be set')

        return inner_function(
            *args,
            **kwargs,
            credentials_lookup=_mk_credentials_lookup(
                image_reference=image_reference,
                privileges=privileges,
            ),
        )

    return outer_function


# kept for backwards-compatibility - todo: rm
_image_exists = _inject_credentials_lookup(inner_function=oci.image_exists)
retrieve_manifest = _inject_credentials_lookup(inner_function=oci.retrieve_manifest)
ls_image_tags = _inject_credentials_lookup(inner_function=oci.tags)
put_blob = _inject_credentials_lookup(inner_function=oci.put_blob)
retrieve_blob = _inject_credentials_lookup(inner_function=oci.get_blob)
cp_oci_artifact = _inject_credentials_lookup(inner_function=oci.replicate_artifact)
put_image_manifest = _inject_credentials_lookup(inner_function=oci.put_image_manifest)
retrieve_container_image = _inject_credentials_lookup(inner_function=oci.retrieve_container_image)


@functools.lru_cache()
def _credentials(image_reference: str, privileges:Privileges=None):
    if privileges:
        privileges = _convert_privileges(privileges=privileges)

    registry_cfg = model.container_registry.find_config(image_reference, privileges)
    if not registry_cfg:
        return None
    credentials = registry_cfg.credentials()
    return docker_creds.Basic(username=credentials.username(), password=credentials.passwd())


def publish_container_image(image_reference: str, image_file_obj, threads=8):
  image_file_obj.seek(0)
  _push_image(
        image_reference=image_reference,
        image_file=image_file_obj.name,
        threads=threads,
    )
  image_file_obj.seek(0)


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


def _mk_credentials(image_reference, privileges: Privileges=None):
  if isinstance(image_reference, str):
    image_reference = docker_name.from_string(name=image_reference)
  try:
    # first try container_registry cfgs from available cfg
    creds = _credentials(image_reference=str(image_reference), privileges=privileges)
    if not creds:
      logger.warning(f'could not find rw-creds for {image_reference}')
      # fall-back to default docker lookup
      creds = docker_creds.DefaultKeychain.Resolve(image_reference)

    return creds
  except Exception as e:
    ci.util.warning(f'Error resolving credentials for {image_reference}: {e}')
    raise e


def to_hash_reference(image_name: str):
  transport = _mk_transport_pool(size=1)

  image_name = normalise_image_reference(image_name)
  image_reference = docker_name.from_string(image_name)
  creds = _mk_credentials(image_reference=image_reference)
  accept = docker_http.SUPPORTED_MANIFEST_MIMES

  digest = None

  with image_list.FromRegistry(image_reference, creds, transport) as img_list:
      if img_list.exists():
          digest = img_list.digest()
      else:
          logger.debug('no manifest found')

  # look for image
  with v2_2_image.FromRegistry(image_reference, creds, transport, accept) as v2_2_img:
      if v2_2_img.exists():
          digest = v2_2_img.digest()
      else:
          logger.debug('no img v2.2 found')

  if not digest:
      # fallback to v2
      with v2_image.FromRegistry(image_reference, creds, transport) as v2_img:
          if v2_img.exists():
              digest = v2_img.digest()
          else:
              logger.debug('no img v2 found')
              raise RuntimeError(f'could not access img-metadata for {image_name}')

  name = image_name.rsplit(':', 1)[0]
  return f'{name}@{digest}'


def _push_image(image_reference: str, image_file: str, threads=8):
  import ci.util
  ci.util.not_none(image_reference)
  ci.util.existing_file(image_file)

  transport = _mk_transport_pool()

  image_reference = normalise_image_reference(image_reference)
  image_reference = docker_name.from_string(image_reference)

  creds = _mk_credentials(
    image_reference=image_reference,
    privileges=Privileges.READ_WRITE,
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


def rm_tag(image_reference: str):
  transport = _mk_transport_pool()
  image_reference = normalise_image_reference(image_reference)
  image_reference = docker_name.from_string(image_reference)
  creds = _mk_credentials(image_reference=image_reference)

  docker_session.Delete(
    name=image_reference,
    creds=creds,
    transport=transport,
    )
  logger.info(f'untagged {image_reference=} - note: did not purge blobs!')
