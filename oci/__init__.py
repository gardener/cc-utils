import dataclasses
import hashlib
import json
import logging
import tarfile
import typing

import dacite
import deprecated

import oci._util as _ou
import oci.auth as oa
import oci.client as oc
import oci.docker as od
import oci.kaniko as ok
import oci.model as om
import oci.util as ou

logger = logging.getLogger(__name__)

# type-alias for typehints
image_reference = str


def image_exists(
    image_reference: str,
    credentials_lookup: oa.credentials_lookup,
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


def tags(
    image_name: str,
    credentials_lookup: oa.credentials_lookup,
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
    credentials_lookup: oa.credentials_lookup,
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
    credentials_lookup: oa.credentials_lookup=None,
    routes: oc.OciRoutes=oc.OciRoutes(),
    oci_client: oc.Client=None,
):
    '''
    verbatimly replicate the OCI Artifact from src -> tgt without taking any assumptions
    about the transported contents. This in particular allows contents to be replicated
    that are not e.g. "docker-compliant" OCI Images.
    '''
    if not (bool(credentials_lookup) ^ bool(oci_client)):
        raise ValueError('either credentials-lookup + routes, xor client must be passed')

    src_image_reference = ou.normalise_image_reference(src_image_reference)
    tgt_image_reference = ou.normalise_image_reference(tgt_image_reference)

    if not oci_client:
        client = oc.Client(
            credentials_lookup=credentials_lookup,
            routes=routes,
        )
    else:
        client = oci_client

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

        head_res = client.head_blob(
            image_reference=tgt_image_reference,
            digest=layer.digest,
        )
        if head_res.ok:
            logger.info(f'skipping blob download {layer.digest=} - already exists in tgt')
            continue # no need to download if blob already exists in tgt

        blob_res = client.blob(
            image_reference=src_image_reference,
            digest=layer.digest,
            absent_ok=is_manifest,
        )
        if not blob_res and is_manifest:
            # fallback to non-verbatim replication; synthesise cfg
            logger.warning(
                'falling back to non-verbatim replication '
                '{src_image_reference=} {tgt_image_reference=}'
            )

            fake_cfg = od.docker_cfg() # TODO: check whether we need to pass-in cfg
            fake_cfg_dict = dataclasses.asdict(fake_cfg)
            fake_cfg_raw = json.dumps(fake_cfg_dict).encode('utf-8')

            client.put_blob(
                image_reference=tgt_image_reference,
                digest=f'sha256:{hashlib.sha256(fake_cfg_raw).hexdigest()}',
                octets_count=len(fake_cfg_raw),
                data=fake_cfg_raw,
            )
            continue

        client.put_blob(
            image_reference=tgt_image_reference,
            digest=layer.digest,
            octets_count=layer.size,
            data=blob_res,
        )

    client.put_manifest(
        image_reference=tgt_image_reference,
        manifest=raw_manifest,
        schema_version=om.OciManifestSchemaVersion(schema_version),
    )


def put_image_manifest(
    image_reference: str, # including tag
    manifest: om.OciImageManifest,
    credentials_lookup: oa.credentials_lookup,
):
    contents = json.dumps(dataclasses.asdict(manifest)).encode('utf-8')
    _ou._put_raw_image_manifest(
        image_reference=image_reference,
        raw_contents=contents,
        credentials_lookup=credentials_lookup,
    )


@deprecated.deprecated
def retrieve_container_image(
    image_reference: str,
    credentials_lookup: oa.credentials_lookup,
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
    credentials_lookup: oa.credentials_lookup,
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

        manifest_bytes = json.dumps(
            dataclasses.asdict(image.oci_manifest())
        ).encode('utf-8')

        for tgt_ref in image_references:
            logger.info(f'publishing manifest {tgt_ref=}')
            oci_client.put_manifest(
                image_reference=tgt_ref,
                manifest=manifest_bytes,
            )


def image_layers_as_tarfile_generator(
    image_reference: str,
    oci_client: oc.Client,
    chunk_size=tarfile.RECORDSIZE,
) -> typing.Generator[bytes, None, None]:
    '''
    returns a generator yielding a tar-archive with the passed oci-image's layer-blobs as
    members. This is somewhat similar to the result of a `docker save` with the notable difference
    that the cfg-blob is discarded.
    This function is useful to e.g. upload file system contents of an oci-container-image to some
    scanning-tool (provided it supports the extraction of tar-archives)
    '''
    manifest = oci_client.manifest(image_reference=image_reference)
    offset = 0
    for blob in manifest.blobs():
        tarinfo = tarfile.TarInfo(name=blob.digest + '.tar') # note: may be gzipped
        tarinfo.size = blob.size
        tarinfo.offset = offset
        tarinfo.offset_data = offset + tarfile.BLOCKSIZE

        offset += blob.size + tarfile.BLOCKSIZE

        tarinfo_bytes = tarinfo.tobuf()
        yield tarinfo_bytes

        uploaded_bytes = len(tarinfo_bytes)
        for chunk in oci_client.blob(
            image_reference=image_reference,
            digest=blob.digest,
            stream=True,
            ).iter_content(chunk_size=chunk_size):
            uploaded_bytes += len(chunk)
            yield chunk

        # need to pad full blocks w/ NUL-bytes
        if (missing := tarfile.BLOCKSIZE - (uploaded_bytes % tarfile.BLOCKSIZE)):
            offset += missing
            yield tarfile.NUL * missing

    # tarchives should be terminated w/ two empty blocks
    yield tarfile.NUL * tarfile.BLOCKSIZE * 2
