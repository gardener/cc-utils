import dataclasses
import enum
import functools
import hashlib
import io
import json
import jsonschema
import logging
import os
import pprint
import subprocess
import sys
import tarfile
import tempfile
import zlib

import dacite
import yaml

import gci.componentmodel as cm
import gci.oci as goci

import ccc.oci
import ci.util
import cnudie.iter
import cnudie.retrieve
import cnudie.upload
import ctx
import tarutil
import oci

own_dir = os.path.dirname(__file__)
repo_root = os.path.join(own_dir, '../..')


_cfg = ctx.cfg

logger = logging.getLogger(__name__)


def edit(
    name: str,
    ocm_repo: str=None,
    editor: str='vim',
):
    if not ocm_repo:
        ocm_repo_lookup = ctx.cfg.ctx.ocm_repository_lookup
        ocm_repo = next(ocm_repo_lookup(name))
        if isinstance(ocm_repo, str):
            ocm_repo = cm.OciOcmRepository(
                baseUrl=ocm_repo,
            )

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
    oci_cfg_len = len(oci_cfg_raw)
    manifest.config = dataclasses.replace(
        manifest.config,
        digest=oci_cfg_dig,
        size=oci_cfg_len,
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
    ocm_repo: str=None,
    format: str='pretty',
):
    if not ocm_repo:
        ocm_lookup = ctx.cfg.ctx.ocm_lookup
    else:
        ocm_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(ocm_repo),
        )

    component_descriptor = ocm_lookup(name)

    if format == 'pretty':
        pprint.pprint(component_descriptor)
    elif format == 'yaml':
        print(
            yaml.dump(
                dataclasses.asdict(component_descriptor),
                Dumper=cm.EnumValueYamlDumper,
            )
        )
    elif format == 'json':
        print(
            json.dumps(
                dataclasses.asdict(component_descriptor),
            )
        )
    else:
        print(f'Error: don\'t know {format=}')
        exit(1)


def artefact(
    name: str,
    artefact_name: str,
    ocm_repo: str=None,
    unzip: bool=True,
    out='-',
):
    if not ocm_repo:
        ocm_lookup = ctx.cfg.ctx.ocm_lookup
    else:
        ocm_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(ocm_repo),
        )

    component = ocm_lookup(name).component

    for artefact in component.iter_artefacts():
        if artefact.name == artefact_name:
            break
    else:
        print(f'did not find {artefact=}')
        exit(1)

    access = artefact.access
    if not isinstance(access, cm.LocalBlobAccess):
        print(f'unsupported {access.type=}')
        print('only localBlobAccess is implemented')
        exit(1)

    access: cm.LocalBlobAccess

    if out == '-' and sys.stdout.isatty():
        print('refusing to print blob to terminal (redirect stdout or set --out)')
        exit(1)

    log = functools.partial(print, file=sys.stderr)

    if out == '-':
        fh = sys.stdout.buffer
    else:
        fh = open(out, 'wb')

    oci_client = ccc.oci.oci_client()

    oci_ref = component.current_ocm_repo.component_oci_ref(component)

    if access.globalAccess:
        digest = access.globalAccess.digest
        size = access.globalAccess.size
    else:
        digest = access.localReference
        size = access.size

    if unzip:
        if access.mediaType.endswith('/gzip'):
            decompressor = zlib.decompressobj(wbits=31)
        else:
            decompressor = None
    else:
        decompressor = None

    log(f'retrieving {size} octets ({digest=})')

    blob = oci_client.blob(
        image_reference=oci_ref,
        digest=digest,
    )
    for chunk in blob.iter_content(4096):
        if decompressor:
            chunk = decompressor.decompress(chunk)

        fh.write(chunk)

    if decompressor:
        fh.write(decompressor.flush())

    fh.flush()
    fh.close()


def upload(
    file: str,
):
    with open(file) as f:
        component_descriptor = cm.ComponentDescriptor.from_dict(
            yaml.safe_load(f)
        )
    component = component_descriptor.component

    target_ocm_repo = component.current_repository_ctx()
    target_ref = target_ocm_repo.component_version_oci_ref(component)

    print(f'will upload to: {target_ref=}')

    cnudie.upload.upload_component_descriptor(
        component_descriptor=component_descriptor,
    )


def traverse(
    name: str,
    version: str=None,
    ocm_repo_url: str=None,
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
    if not ocm_repo_url:
        ocm_repo_lookup = _cfg.ctx.ocm_repository_lookup
    else:
        ocm_repo_lookup = cnudie.retrieve.ocm_repository_lookup(ocm_repo_url)

    if not ocm_repo_lookup:
        print('must pass --ocm-repo-url')
        exit(1)

    if not version:
        name, version = name.rsplit(':', 1)

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=ocm_repo_lookup,
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

            if not eval(filter_expr, { # nosec B307
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
                print(eval(print_expr, {'node': node, 'artefact': None})) # nosec B307
        if isinstance(node, cnudie.iter.ResourceNode):
            if not print_expr:
                prefix = 'r'
                indent += 1
                print(f'{prefix}{" " * indent}{node.resource.name}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': node.resource})) # nosec B307
        if isinstance(node, cnudie.iter.SourceNode):
            if not print_expr:
                prefix = 'r'
                indent += 1
                print(f'{prefix}{" " * indent}{node.source.name}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': node.source})) # nosec B307


def validate(component_descriptor: str):
    schema_file = os.path.join(
        repo_root,
        'gci',
        'component-descriptor-v2-schema.yaml',
    )
    with open(schema_file) as f:
        schema_dict = yaml.safe_load(f)

    with open(component_descriptor) as f:
        comp_dict = yaml.safe_load(f)

    jsonschema.validate(
        instance=comp_dict,
        schema=schema_dict,
    )

    print('schema validation succeeded')


def add_labels(
    component_descriptor_src_file: str,
    component_descriptor_out_file: str=None,
    labels: list[str]=[],
):
    component_descriptor = cm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_src_file)
    )

    for raw_label in labels:
        parsed_label = yaml.safe_load(raw_label)
        label = cm.Label(
            name=parsed_label.get('name'),
            value=parsed_label.get('value'),
        )
        component_descriptor.component.labels.append(label)

    if component_descriptor_out_file:
        outfh = open(component_descriptor_out_file, 'w')
    else:
        outfh = sys.stdout

    yaml.dump(
        data=dataclasses.asdict(component_descriptor),
        Dumper=cm.EnumValueYamlDumper,
        stream=outfh,
    )
