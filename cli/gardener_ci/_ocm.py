import dataclasses
import functools
import json
import jsonschema
import logging
import os
import pprint
import sys
import zlib

import yaml

import ocm
import ocm.__main__
import ocm.iter
import ocm.oci
import ocm.upload

import ccc.oci
import ci.util
import cnudie.retrieve
import ctx

own_dir = os.path.dirname(__file__)
repo_root = os.path.join(own_dir, '../..')


__cmd_name__ = 'ocm'
_cfg = ctx.cfg

logger = logging.getLogger(__name__)


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
            oci_client=ccc.oci.oci_client(),
        )

    component_descriptor = ocm_lookup(name)

    if format == 'pretty':
        pprint.pprint(component_descriptor)
    elif format == 'yaml':
        print(
            yaml.dump(
                dataclasses.asdict(component_descriptor),
                Dumper=ocm.EnumValueYamlDumper,
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
            oci_client=ccc.oci.oci_client(),
        )

    component = ocm_lookup(name).component

    for artefact in component.iter_artefacts():
        if artefact.name == artefact_name:
            break
    else:
        print(f'did not find {artefact=}')
        exit(1)

    access = artefact.access
    if not isinstance(access, ocm.LocalBlobAccess):
        print(f'unsupported {access.type=}')
        print('only localBlobAccess is implemented')
        exit(1)

    access: ocm.LocalBlobAccess

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
    overwrite: bool=False,
):
    with open(file) as f:
        component_descriptor = ocm.ComponentDescriptor.from_dict(
            yaml.safe_load(f)
        )
    component = component_descriptor.component

    target_ocm_repo = component.current_ocm_repo
    target_ref = target_ocm_repo.component_version_oci_ref(component)

    print(f'will upload to: {target_ref=}')

    oci_client = ccc.oci.oci_client()

    if overwrite:
        on_exist = ocm.upload.UploadMode.OVERWRITE
    else:
        on_exist = ocm.upload.UploadMode.FAIL

    ocm.upload.upload_component_descriptor(
        component_descriptor=component_descriptor,
        oci_client=oci_client,
        on_exist=on_exist,
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
        oci_client=ccc.oci.oci_client(),
    )

    component = component_descriptor_lookup(ocm.ComponentIdentity(
        name=name,
        version=version,
    )).component

    ocm.__main__.traverse(
        component=component,
        components=components,
        sources=sources,
        resources=resources,
        component_descriptor_lookup=component_descriptor_lookup,
        print_expr=print_expr,
        filter_expr=filter_expr,
        output_format='pretty',
    )


def validate(component_descriptor: str):
    schema_file = os.path.join(
        repo_root,
        'ocm',
        'ocm-component-descriptor-schema.yaml',
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
    component_descriptor = ocm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_src_file)
    )

    for raw_label in labels:
        parsed_label = yaml.safe_load(raw_label)
        label = ocm.Label(
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
        Dumper=ocm.EnumValueYamlDumper,
        stream=outfh,
    )
