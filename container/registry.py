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

from containerregistry.client import docker_creds
from containerregistry.client import docker_name
from containerregistry.transport import retry
from containerregistry.transport import transport_pool

import httplib2

logger = logging.getLogger(__name__)


def _mk_credentials_lookup(
    image_reference: str,
    privileges: oa.Privileges=oa.Privileges.READONLY,
):
    def find_credentials(image_reference, privileges, absent_ok):
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
        privileges=oa.Privileges.READONLY,
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
cp_oci_artifact = _inject_credentials_lookup(inner_function=oci.replicate_artifact)
retrieve_container_image = _inject_credentials_lookup(inner_function=oci.retrieve_container_image)
publish_container_image = _inject_credentials_lookup(inner_function=oci.publish_container_image)


@functools.lru_cache()
def _credentials(image_reference: str, privileges: oa.Privileges=None):
    registry_cfg = model.container_registry.find_config(image_reference, privileges)
    if not registry_cfg:
        return None
    credentials = registry_cfg.credentials()
    return docker_creds.Basic(username=credentials.username(), password=credentials.passwd())


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


def _mk_credentials(image_reference, privileges: oa.Privileges=None):
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
