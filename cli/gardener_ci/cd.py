import concurrent.futures
import dataclasses
import hashlib
import io
import json
import logging
import subprocess
import sys
import tarfile
import tempfile

import dacite

import gci.componentmodel as cm
import gci.oci as goci

import ccc.oci
import cnudie.iter
import cnudie.purge
import cnudie.retrieve
import cnudie.util
import cnudie.validate
import ctx
import oci.model
import tarutil
import version

logger = logging.getLogger(__name__)


def edit(
    name: str,
    ocm_repo: str=None,
    editor: str='vim',
):
    if not ocm_repo:
        ocm_repo = ctx.cfg.ctx.ocm_repo_base_url

    oci_client = ccc.oci.oci_client()

    oci_ref = cnudie.util.oci_ref(
        component=name,
        repository=ocm_repo,
    )

    logger.info(f'retrieving {oci_ref} oci--manifest')
    manifest = oci_client.manifest(oci_ref)

    oci_cfg: goci.ComponentDescriptorOciCfg = dacite.from_dict(
        data_class=goci.ComponentDescriptorOciCfg,
        data=json.loads(
            oci_client.blob(oci_ref, manifest.config.digest).text,
        )
    )

    logger.info(f'retrieving component-descriptor-blob {oci_cfg.componentDescriptorLayer.digest=}')
    component_descriptor_blob = oci_client.blob(
        oci_ref,
        oci_cfg.componentDescriptorLayer.digest,
    )

    tar = tarfile.open(
        fileobj=tarutil.FilelikeProxy(
            component_descriptor_blob.iter_content(chunk_size=tarfile.BLOCKSIZE)
        ),
        mode='r|*',
    )

    # component-descriptor must be first entry in tarfile
    component_descriptor_info = tar.next()

    with tempfile.NamedTemporaryFile() as tf:
        reader = tar.extractfile(component_descriptor_info)
        digest = hashlib.sha256()

        while chunk := reader.read(tarfile.BLOCKSIZE):
            tf.write(chunk)
            digest.update(chunk)

        tar.close()
        tf.flush()

        old_content_digest = digest.hexdigest()

        subprocess.run((editor, tf.name))

        tf.seek(0)
        raw = tf.read()
        if (content_digest := hashlib.sha256(raw).hexdigest()) == old_content_digest:
            print('no changes - early-exiting')
            exit(0)

    logger.info(f'uploading changed component-descriptor {content_digest=}')

    # we need to know the digest before uploading - since tarheader does not add much overhead,
    # do this in-memory
    buf = io.BytesIO()
    tar = tarfile.open(fileobj=buf, mode='w')

    info = tarfile.TarInfo(name='component-descriptor.yaml')
    info.size = len(raw)
    tar.addfile(info, io.BytesIO(raw))
    tf_len = buf.tell()
    buf.seek(0)

    # prefix w/ algorithm, as expected by oci-registry
    content_digest = f'sha256:{hashlib.sha256(buf.read()).hexdigest()}'
    buf.seek(0)

    oci_client.put_blob(oci_ref, content_digest, tf_len, buf)

    # replace old layer w/ updated one
    manifest.layers = [
        l for l in manifest.layers
        if not l.digest == oci_cfg.componentDescriptorLayer.digest
    ] + [oci.model.OciBlobRef(
        digest=content_digest,
        mediaType=goci.component_descriptor_mimetype,
        size=tf_len,
    )]

    oci_cfg.componentDescriptorLayer.digest = content_digest
    oci_cfg_raw = json.dumps(dataclasses.asdict(oci_cfg)).encode('utf-8')
    oci_cfg_dig = f'sha256:{hashlib.sha256(oci_cfg_raw).hexdigest()}'
    manifest.config = dataclasses.replace(
        manifest.config,
        digest=oci_cfg_dig,
    )

    logger.info(f'uploading patched cfg-blob {oci_cfg_dig=}')
    oci_client.put_blob(oci_ref, oci_cfg_dig, len(oci_cfg_raw), oci_cfg_raw)

    # finally, finish upload by pushing patched manifest
    oci_client.put_manifest(
        oci_ref,
        manifest=json.dumps(manifest.as_dict())
    )


def retrieve(
    name: str,
    version: str=None,
    ocm_repo: str=None,
    out: str=None
):
    if not ocm_repo:
        ocm_repo = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(
            baseUrl=ocm_repo,
        )

    if not version:
        name, version = name.rsplit(':', 1)

    component_descriptor = cnudie.retrieve.oci_component_descriptor_lookup()(
        component_id=cm.ComponentIdentity(
            name=name,
            version=version,
        ),
        ctx_repo=ctx_repo,
    )

    if not component_descriptor:
        print(f'Error: did not find {name}:{version}')
        exit(1)

    if out:
        outfh = open(out, 'w')
    else:
        outfh = sys.stdout

    component_descriptor.to_fobj(fileobj=outfh)
    outfh.flush()
    outfh.close()


def validate(
    name: str,
    version: str,
    ctx_base_url: str=None,
    out: str=None
):
    if not ctx_base_url:
        ctx_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(
        baseUrl=ctx_base_url,
    )

    logger.info('retrieving component-descriptor..')
    component_descriptor = cnudie.retrieve.oci_component_descriptor_lookup()(
        component_id=cm.ComponentIdentity(
            name=name,
            version=version,
        ),
        ctx_repo=ctx_repo,
    )
    component = component_descriptor.component
    logger.info('validating component-descriptor..')

    violations = tuple(
        cnudie.validate.iter_violations(
            nodes=cnudie.iter.iter(
                component=component,
                recursion_depth=0,
            ),
        )
    )

    if not violations:
        logger.info('component-descriptor looks good')
        return

    logger.warning('component-descriptor yielded validation-errors (see below)')
    print()

    for violation in violations:
        print(violation.as_error_message)


def ls(
    name: str,
    greatest: bool=False,
    final: bool=False,
    ocm_repo_base_url: str=None,
):
    if not ocm_repo_base_url:
        ocm_repo_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(baseUrl=ocm_repo_base_url)

    if greatest:
        print(cnudie.retrieve.greatest_component_version(
            component_name=name,
            ctx_repo=ctx_repo,
        ))
        return

    versions = cnudie.retrieve.component_versions(
        component_name=name,
        ctx_repo=ctx_repo,
    )

    for v in versions:
        if final:
            parsed_version = version.parse_to_semver(v)
            if parsed_version.prerelease:
                continue
        print(v)


def purge_old(
    name: str,
    final: bool=False,
    repo_base_url: str=None,
    keep: int=256,
    threads: int=32,
):
    if not repo_base_url:
        repo_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(baseUrl=repo_base_url)

    versions = cnudie.retrieve.component_versions(
        component_name=name,
        ctx_repo=ctx_repo,
    )

    if not final:
        versions = [
            v for v in versions
            if not version.parse_to_semver(v).prerelease
        ]

    versions = version.smallest_versions(
        versions=versions,
        keep=keep,
    )

    print(f'will rm {len(versions)} version(s) using {threads=}')

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=threads)
    oci_client = ccc.oci.oci_client(
        http_connection_pool_size=threads,
    )

    def purge_component_descriptor(ref: str):
        oci_client.delete_manifest(
            image_reference=ref,
            purge=True,
        )
        print(f'purged: {ref}')

    def iter_oci_refs_to_rm():
        for v in versions:
            ref = f'{repo_base_url}/component-descriptors/{name}:{v}'
            yield pool.submit(
                purge_component_descriptor,
                ref=ref,
            )

    for ref in concurrent.futures.as_completed(iter_oci_refs_to_rm()):
        pass


def purge(
    name: str,
    recursive: bool=False,
    version: str=None,
    repo_base_url: str=None,
):
    if not version:
        name, version = name.rsplit(':', 1)

    if not repo_base_url:
        repo_base_url = ctx.cfg.ctx.ocm_repo_base_url

    lookup = cnudie.retrieve.oci_component_descriptor_lookup()

    component_descriptor = lookup(
        component_id=cm.ComponentIdentity(
            name=name,
            version=version,
        ),
        ctx_repo=cm.OciRepositoryContext(baseUrl=repo_base_url),
    )

    oci_client = ccc.oci.oci_client()

    cnudie.purge.remove_component_descriptor_and_referenced_artefacts(
        component=component_descriptor.component,
        oci_client=oci_client,
        lookup=lookup,
        recursive=recursive,
    )
