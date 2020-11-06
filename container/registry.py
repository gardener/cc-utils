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


import contextlib
import dataclasses
import functools
import hashlib
import io
import json
import logging
import tarfile
import tempfile
import typing

import dacite

import ci.util
import model.container_registry
from model.container_registry import Privileges

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

import httplib2

logger = logging.getLogger(__name__)

_DEFAULT_TAG = 'i-was-a-digest'

_PROCESSOR_ARCHITECTURE = 'amd64'

_OPERATING_SYSTEM = 'linux'


class OciImageNotFoundException(Exception):
    pass


@dataclasses.dataclass
class OciBlobRef:
    digest: str
    mediaType: str
    size: int


@dataclasses.dataclass
class OciImageManifest:
    config: OciBlobRef
    layers: typing.Sequence[OciBlobRef]
    mediaType: str = docker_http.MANIFEST_SCHEMA2_MIME
    schemaVersion: int = 2


# Today save.tarball expects a tag, which is emitted into one or more files
# in the resulting tarball.  If we don't translate the digest into a tag then
# the tarball format leaves us no good way to represent this information and
# folks are left having to tag the resulting image ID (yuck).  As a datapoint
# `docker save -o /tmp/foo.tar bar@sha256:deadbeef` omits the v1 "repositories"
# file and emits `null` for the `RepoTags` key in "manifest.json".  By doing
# this we leave a trivial breadcrumb of what the image was named (and the digest
# is recoverable once the image is loaded), which is a strictly better UX IMO.
# We do not need to worry about collisions by doing this here because this tool
# only packages a single image, so this is preferable to doing something similar
# in save.py itself.
def _make_tag_if_digest(
    name):
  if isinstance(name, docker_name.Tag):
    return name
  return docker_name.Tag('{repo}:{tag}'.format(
      repo=str(name.as_repository()), tag=_DEFAULT_TAG))


def normalise_image_reference(image_reference):
  ci.util.check_type(image_reference, str)
  if '@' in image_reference:
    return image_reference

  parts = image_reference.split('/')

  left_part = parts[0]
  # heuristically check if we have a (potentially) valid hostname
  if '.' not in left_part.split(':')[0]:
    # insert 'library' if only image name was given
    if len(parts) == 1:
      parts.insert(0, 'library')

    # probably, the first part is not a hostname; inject default registry host
    parts.insert(0, 'registry-1.docker.io')

  # of course, docker.io gets special handling
  if parts[0] == 'docker.io':
      parts[0] = 'registry-1.docker.io'

  return '/'.join(parts)


@functools.lru_cache()
def _credentials(image_reference: str, privileges:Privileges=None):
    ci.util.check_type(image_reference, str)
    registry_cfg = model.container_registry.find_config(image_reference, privileges)
    if not registry_cfg:
        return None
    credentials = registry_cfg.credentials()
    return docker_creds.Basic(username=credentials.username(), password=credentials.passwd())


def _mk_transport(
    image_name: str,
    action: str=docker_http.PULL,
    privileges: Privileges=Privileges.READ_ONLY,
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


def ls_image_tags(image_name: str):
    transport = _mk_transport(
        image_name=image_name,
        action=docker_http.PULL,
    )

    if isinstance(image_name, str):
        image_name = docker_name.from_string(image_name)

    url = f'https://{image_name.registry}/v2/{image_name.repository}/tags/list'

    res, body_bytes = transport.Request(url, (200,))
    parsed = json.loads(body_bytes)

    # XXX parsed['manifest'] might be used to e.g. determine stale images, and purge them
    tags = parsed['tags']
    return tags


def put_blob(
    image_name: str,
    fileobj: typing.BinaryIO,
    mimetype: str='application/octet-stream',
):
    '''
    uploads the given blob to the specified namespace / target OCI registry

    Note that the blob will be read into main memory; not suitable for larget contents.
    '''
    fileobj.seek(0)
    sha256_hash = hashlib.sha256()
    while (chunk := fileobj.read(4096)):
        sha256_hash.update(chunk)
    sha256_digest = sha256_hash.hexdigest()
    fileobj.seek(0)
    print(f'{sha256_digest=}')

    image_ref = image_name
    image_name = docker_name.from_string(image_name)
    contents = fileobj.read()

    push_sess = docker_session.Push(
        name=image_name,
        creds=_mk_credentials(
            image_reference=image_ref,
            privileges=Privileges.READ_WRITE,
        ),
        transport=_mk_transport_pool(),
    )

    print(f'{len(contents)=}')
    # XXX superdirty hack - force usage of our blob :(
    push_sess._get_blob = lambda a,b: contents
    push_sess._patch_upload(
        image_name,
        f'sha256:{sha256_digest}',
    )
    print(f'successfully pushed {image_name=} {sha256_digest=}')

    return sha256_digest


def _put_raw_image_manifest(
    image_reference: str,
    raw_contents: bytes,
):
    image_name = docker_name.from_string(image_reference)

    push_sess = docker_session.Push(
        name=image_name,
        creds=_mk_credentials(
            image_reference=image_reference,
            privileges=Privileges.READ_WRITE,
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


def put_image_manifest(
    image_reference: str, # including tag
    manifest: OciImageManifest,
):
    contents = json.dumps(dataclasses.asdict(manifest)).encode('utf-8')
    _put_raw_image_manifest(
        image_reference=image_reference,
        raw_contents=contents,
    )


def retrieve_container_image(image_reference: str, outfileobj=None):
  tmp_file = _pull_image(image_reference=image_reference, outfileobj=outfileobj)
  tmp_file.seek(0)
  return tmp_file


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


def _image_exists(image_reference: str) -> bool:
  transport = _mk_transport_pool(size=1)

  image_reference = normalise_image_reference(image_reference)
  image_reference = docker_name.from_string(image_reference)
  creds = _mk_credentials(image_reference=image_reference)
  accept = docker_http.SUPPORTED_MANIFEST_MIMES

  with image_list.FromRegistry(image_reference, creds, transport) as img_list:
      if img_list.exists():
          return True
      logger.debug('no manifest found')

  # look for image
  with v2_2_image.FromRegistry(image_reference, creds, transport, accept) as v2_2_img:
      if v2_2_img.exists():
          return True
      logger.debug('no img v2.2 found')

  return False


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


def _tag_or_digest_reference(image_reference):
  if isinstance(image_reference, str):
    image_reference = docker_name.from_string(image_reference)
  ref_type = type(image_reference)
  if ref_type in (docker_name.Tag, docker_name.Digest):
    return True
  raise ValueError(f'{image_reference=} is does not contain a symbolic or hash tag')


@contextlib.contextmanager
def pulled_image(image_reference: str):
  ci.util.not_none(image_reference)
  _tag_or_digest_reference(image_reference)

  transport = _mk_transport_pool()
  image_reference = normalise_image_reference(image_reference)
  image_reference = docker_name.from_string(image_reference)
  creds = _mk_credentials(image_reference=image_reference)

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

    raise OciImageNotFoundException(f'failed to retrieve {image_reference=} - does it exist?')

  except Exception as e:
    raise e


def _pull_image(image_reference: str, outfileobj=None):
  import ci.util
  ci.util.not_none(image_reference)
  image_reference = normalise_image_reference(image_reference=image_reference)

  outfileobj = outfileobj if outfileobj else tempfile.TemporaryFile()

  with tarfile.open(fileobj=outfileobj, mode='w:') as tar:
    with pulled_image(image_reference=image_reference) as image:
      image_reference = docker_name.from_string(image_reference)
      save.tarball(_make_tag_if_digest(image_reference), image, tar)
      return outfileobj


def _retrieve_raw_manifest(
    image_reference: str,
    absent_ok: bool=False,
):
  image_reference = normalise_image_reference(image_reference=image_reference)
  try:
    with pulled_image(image_reference=image_reference) as image:
      return image.manifest()
  except OciImageNotFoundException as oie:
    if absent_ok:
      return None
    raise oie


def retrieve_manifest(
    image_reference: str,
    absent_ok: bool=False,
) -> OciImageManifest:
  try:
    raw_dict = json.loads(
        _retrieve_raw_manifest(
            image_reference=image_reference,
            absent_ok=False,
        )
    )
    manifest = dacite.from_dict(
      data_class=OciImageManifest,
      data=raw_dict,
    )

    return manifest
  except OciImageNotFoundException as oie:
    if absent_ok:
      return None
    raise oie


def retrieve_blob(image_reference: str, digest: str) -> bytes:
  with pulled_image(image_reference=image_reference) as image:
      return image.blob(digest)


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


def cp_oci_artifact(
    src_image_reference: str,
    tgt_image_reference: str,
):
    '''
    verbatimly replicate the OCI Artifact from src -> tgt without taking any assumptions
    about the transported contents. This in particular allows contents to be replicated
    that are not e.g. "docker-compliant" OCI Images.
    '''
    src_image_reference = normalise_image_reference(src_image_reference)
    tgt_image_reference = normalise_image_reference(tgt_image_reference)

    # we need the unaltered - manifest for verbatim replication
    raw_manifest = _retrieve_raw_manifest(image_reference=src_image_reference)
    manifest = dacite.from_dict(
        data_class=OciImageManifest,
        data=json.loads(raw_manifest)
    )

    for layer in manifest.layers + [manifest.config]:
        # XXX we definitely should _not_ read entire blobs into memory
        # this is done by the used containerregistry lib, so we do not make things worse
        # here - however this must not remain so!
        blob = io.BytesIO(
            retrieve_blob(
                image_reference=src_image_reference,
                digest=layer.digest,
            )
        )
        put_blob(
            image_name=tgt_image_reference,
            fileobj=blob,
        )

    _put_raw_image_manifest(
        image_reference=tgt_image_reference,
        raw_contents=raw_manifest.encode('utf-8'),
    )
