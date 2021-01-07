import dataclasses
import hashlib
import json
import logging
import tempfile
import typing

import dacite

import oci._util as _ou
import oci.auth as oa
import oci.client as oc
import oci.kaniko as ok
import oci.model as om
import oci.util as ou

logger = logging.getLogger(__name__)

# type-alias for typehints
image_reference = str


def image_exists(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
) -> bool:
    '''
    returns a boolean value indicating whether or not the given OCI Artifact exists
    '''
    transport = _ou._mk_transport_pool(size=1)

    image_reference = ou.normalise_image_reference(image_reference=image_reference)
    image_reference = _ou.docker_name.from_string(image_reference)
    creds = _ou._mk_credentials(
        image_reference=image_reference,
        credentials_lookup=credentials_lookup,
    )

    # keep import local to avoid exposure to module's users
    from containerregistry.client.v2_2 import docker_image_list as image_list

    with image_list.FromRegistry(image_reference, creds, transport) as img_list:
        if img_list.exists():
            return True

    # keep import local to avoid exposure to module's users
    from containerregistry.client.v2_2 import docker_image as v2_2_image

    accept = _ou.docker_http.SUPPORTED_MANIFEST_MIMES
    with v2_2_image.FromRegistry(image_reference, creds, transport, accept) as v2_2_img:
        if v2_2_img.exists():
            return True

    return False


def retrieve_manifest(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
    absent_ok: bool=False,
) -> om.OciImageManifest:
  '''
  retrieves the OCI Artifact manifest for the specified reference, and returns it in a
  deserialised form.
  '''
  client = oc.Client(credentials_lookup=credentials_lookup)
  try:
    return client.manifest(image_reference=image_reference)
  except om.OciImageNotFoundException as oie:
    if absent_ok:
      return None
    raise oie


def tags(
    image_name: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
) -> typing.Sequence[str]:
    '''
    returns a sequence of all `tags` for the given image_name
    '''
    if isinstance(image_name, str):
        image_name = ou.normalise_image_reference(image_name)

    from containerregistry.client.v2_2 import docker_http
    transport = _ou._mk_transport(
        image_name=image_name,
        credentials_lookup=credentials_lookup,
        action=docker_http.PULL,
    )

    if isinstance(image_name, str):
        from containerregistry.client import docker_name
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
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
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
    logger.debug(f'{sha256_digest=}')

    image_ref = image_name
    image_name = _ou.docker_name.from_string(image_name)
    contents = fileobj.read()

    from containerregistry.client.v2_2 import docker_session
    push_sess = docker_session.Push(
        name=image_name,
        creds=_ou._mk_credentials(
            image_reference=image_ref,
            privileges=oa.Privileges.READWRITE,
            credentials_lookup=credentials_lookup,
        ),
        transport=_ou._mk_transport_pool(),
    )

    logger.debug(f'{len(contents)=}')
    # XXX superdirty hack - force usage of our blob :(
    push_sess._get_blob = lambda a,b: contents
    push_sess._patch_upload(
        image_name,
        f'sha256:{sha256_digest}',
    )
    logger.debug(f'successfully pushed {image_name=} {sha256_digest=}')

    return sha256_digest


def replicate_artifact(
    src_image_reference: str,
    tgt_image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
):
    '''
    verbatimly replicate the OCI Artifact from src -> tgt without taking any assumptions
    about the transported contents. This in particular allows contents to be replicated
    that are not e.g. "docker-compliant" OCI Images.
    '''
    src_image_reference = ou.normalise_image_reference(src_image_reference)
    tgt_image_reference = ou.normalise_image_reference(tgt_image_reference)

    client = oc.Client(credentials_lookup=credentials_lookup)

    # we need the unaltered - manifest for verbatim replication
    raw_manifest = client.manifest_raw(
        image_reference=src_image_reference,
    ).text
    manifest = json.loads(raw_manifest)
    schema_version = int(manifest['schemaVersion'])
    if schema_version == 1:
        manifest = dacite.from_dict(
            data_class=om.OciImageManifestV1,
            data=json.loads(raw_manifest)
        )
        manifest = client.manifest(src_image_reference)
    elif schema_version == 2:
        manifest = dacite.from_dict(
            data_class=om.OciImageManifest,
            data=json.loads(raw_manifest)
        )

    for idx, layer in enumerate(manifest.blobs()):
        # need to specially handle manifest (may be absent for v2 / legacy images)
        is_manifest = idx == 0

        blob_res = client.blob(
            image_reference=src_image_reference,
            digest=layer.digest,
            absent_ok=is_manifest,
        )
        if not blob_res:
            # fallback to non-verbatim replication
            # XXX we definitely should _not_ read entire blobs into memory
            # this is done by the used containerregistry lib, so we do not make things worse
            # here - however this must not remain so!
            logger.warning(
                'falling back to non-verbatim replication '
                '{src_image_reference=} {tgt_image_reference=}'
            )
            with tempfile.NamedTemporaryFile() as tmp_fh:
                retrieve_container_image(
                    image_reference=src_image_reference,
                    credentials_lookup=credentials_lookup,
                    outfileobj=tmp_fh,
                )
                publish_container_image(
                    image_reference=tgt_image_reference,
                    image_file_obj=tmp_fh,
                    credentials_lookup=credentials_lookup,
                )
            return

        client.put_blob(
            image_reference=tgt_image_reference,
            digest=layer.digest,
            octets_count=layer.size,
            data=blob_res,
        )

    client.put_manifest(
        image_reference=tgt_image_reference,
        manifest=raw_manifest,
    )


def put_image_manifest(
    image_reference: str, # including tag
    manifest: om.OciImageManifest,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
):
    contents = json.dumps(dataclasses.asdict(manifest)).encode('utf-8')
    _ou._put_raw_image_manifest(
        image_reference=image_reference,
        raw_contents=contents,
        credentials_lookup=credentials_lookup,
    )


def retrieve_container_image(
    image_reference: str,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
    outfileobj=None,
):
  tmp_file = _ou._pull_image(
      image_reference=image_reference,
      outfileobj=outfileobj,
      credentials_lookup=credentials_lookup,
  )
  tmp_file.seek(0)
  return tmp_file


def publish_container_image(
    image_reference: str,
    image_file_obj,
    credentials_lookup: typing.Callable[[image_reference, oa.Privileges, bool], oa.OciConfig],
    threads=8
):
  image_file_obj.seek(0)
  _ou._push_image(
        image_reference=image_reference,
        image_file=image_file_obj.name,
        credentials_lookup=credentials_lookup,
        threads=threads,
    )
  image_file_obj.seek(0)


def publish_container_image_from_kaniko_tarfile(
    image_tarfile_path: str,
    oci_client: oc.Client,
    image_reference: str,
    additional_tags: typing.List[str]=(),
):
    image_reference = ou.normalise_image_reference(image_reference=image_reference)
    image_name = image_reference.rsplit(':', 1)[0]
    image_references = (image_reference,) + tuple([f'{image_name}:{tag}' for tag in additional_tags])

    with ok.read_kaniko_image_tar(tar_path=image_tarfile_path) as image:
        chunk_size = 1024 * 1024
        for kaniko_blob in image.blobs():
            oci_client.put_blob(
                image_reference=image_reference,
                digest=kaniko_blob.digest_str(),
                octets_count=kaniko_blob.size,
                data=kaniko_blob,
                max_chunk=chunk_size,
            )

            oci_client.blob(
                image_reference=image_reference,
                digest=kaniko_blob.digest_str(),
                absent_ok=True,
            )

        manifest_bytes = json.dumps(
            dataclasses.asdict(image.oci_manifest())
        ).encode('utf-8')

        for tgt_ref in image_references:
            logger.info(f'publishing manifest {tgt_ref=}')
            oci_client.put_manifest(
                image_reference=tgt_ref,
                manifest=manifest_bytes,
            )
