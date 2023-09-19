import dataclasses
import enum
import hashlib
import io
import json
import logging
import os
import subprocess
import tarfile
import tempfile

import dacite

import gci.componentmodel as cm
import gci.oci as goci

import ccc.oci
import cnudie.iter
import cnudie.retrieve
import ctx
import tarutil
import oci


_cfg = ctx.cfg

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

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        reader = tar.extractfile(component_descriptor_info)
        digest = hashlib.sha256()

        while chunk := reader.read(tarfile.BLOCKSIZE):
            tf.write(chunk)
            digest.update(chunk)

        tar.close()
        tf.flush()

        old_content_digest = digest.hexdigest()

        subprocess.run((editor, tf.name))

    # vi (re)creates files on write (with default backup/write settings), -> (re)open filehandle
    with open(tf.name, 'rb') as tf:
        raw = tf.read()
        if (content_digest := hashlib.sha256(raw).hexdigest()) == old_content_digest:
            print('no changes - early-exiting')
            exit(0)

    os.unlink(tf.name)

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


def traverse(
    name: str,
    version: str=None,
    ctx_base_url: str=None,
    components: bool=True,
    sources: bool=True,
    resources: bool=True,
    print_expr: str=None,
    filter_expr: str=None,
):
    '''
    name: either component-name, or <component-name>:<version>
    version: optional, if not passed w/ name (no value-checking will be done!)
    components: whether to print components
    sources: whether to print sources
    resources: whether to print resources
    print_expr: python-expression (passed to `eval()` w/ globals: {'node': node})
    '''
    if not ctx_base_url:
        ctx_base_url = _cfg.ctx.ocm_repo_base_url

    if not ctx_base_url:
        print('must pass --ctx-base-url')
        exit(1)

    if not version:
        name, version = name.rsplit(':', 1)

    ctx_repo = cm.OciRepositoryContext(
        baseUrl=ctx_base_url,
    )

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        default_ctx_repo=ctx_repo,
    )

    component_descriptor = component_descriptor_lookup(cm.ComponentIdentity(
        name=name,
        version=version,
    ))
    component = component_descriptor.component

    for node in cnudie.iter.iter(
        component=component,
        lookup=component_descriptor_lookup,
    ):
        indent = len(node.path * 2)

        is_component_node = isinstance(node, cnudie.iter.ComponentNode)
        is_source_node = isinstance(node, cnudie.iter.SourceNode)
        is_resource_node = isinstance(node, cnudie.iter.ResourceNode)

        if is_component_node and not components:
            continue

        if is_source_node and not sources:
            continue

        if is_resource_node and not resources:
            continue

        if filter_expr:
            if is_component_node:
                typestr = 'component'
                artefact = None
            elif is_source_node:
                typestr = node.source.type
                artefact = node.source
            elif is_resource_node:
                typestr = node.resource.type
                artefact = node.resource

            if isinstance(typestr, enum.Enum):
                typestr = typestr.value

            if not eval(filter_expr, {
                'node': node,
                'type': typestr,
                'artefact': artefact,
            }):
                continue

        if isinstance(node, cnudie.iter.ComponentNode):
            if not print_expr:
                prefix = 'c'
                print(f'{prefix}{" " * indent}{node.component.name}:{node.component.version}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': None}))
        if isinstance(node, cnudie.iter.ResourceNode):
            if not print_expr:
                prefix = 'r'
                indent += 1
                print(f'{prefix}{" " * indent}{node.resource.name}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': node.resource}))
        if isinstance(node, cnudie.iter.SourceNode):
            if not print_expr:
                prefix = 'r'
                indent += 1
                print(f'{prefix}{" " * indent}{node.source.name}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': node.source}))
