import argparse
import collections.abc
import dataclasses
import datetime
import enum
import io
import json
import os
import sys
import textwrap

import dacite

import cnudie.retrieve
import ctt.__main__
import oci.client
import ocm
import ocm.gardener
import ocm.iter
import ocm.oci
import ocm.upload

own_dir = os.path.dirname(__file__)


try:
    import yaml
    _have_yaml = True
except ImportError:
    _have_yaml = False

try:
    import oci
    import oci.auth
    import oci.client
    _have_oci = True
except ImportError:
    _have_oci = False


if _have_yaml:
    _yaml_or_json_load = yaml.safe_load
else:
    _yaml_or_json_load = json.load


def _parse_yaml_or_json(path) -> dict | list:
    with open(path) as f:
        return _yaml_or_json_load(f)


def dump(component_descriptor: ocm.ComponentDescriptor, parsed):
    if parsed.out == '-':
        outfh = sys.stdout
    else:
        outfh = open(parsed.out, 'w')

    raw_dict = dataclasses.asdict(component_descriptor)

    if _have_yaml:
        yaml.dump(
            data=raw_dict,
            stream=outfh,
            Dumper=ocm.EnumValueYamlDumper,
        )
    else:
        json.dump(
            obj=raw_dict,
            fp=outfh,
            cls=ocm.EnumJSONEncoder,
        )
    outfh.flush()


def _iter_parsed_labels(labels) -> collections.abc.Generator[ocm.Label, None, None]:
    '''
    parses the passed labels (which is expected to be str-instances in YAML/JSON format) into
    OCM-Labels.
    '''
    for label in labels:
        if os.path.exists(label):
            label = _parse_yaml_or_json(label)
        else:
            label = _yaml_or_json_load(label)

        if isinstance(label, list):
            label_entries = label
        elif isinstance(label, dict):
            label_entries = (label,)
        else:
            print(f'Error: --label must be either an array, or an object. got: {label=}')
            exit(1)

        for label in label_entries:
            yield ocm.Label(
                name=label['name'],
                value=label['value'],
            )


def create(parsed):
    now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec='seconds',
    ).removesuffix('+00:00') + 'Z'

    if parsed.ocm_repo:
        ocm_repos = [
            ocm.OciOcmRepository(baseUrl=parsed.ocm_repo),
        ]
    else:
        ocm_repos = []

    component_descriptor = ocm.ComponentDescriptor(
        meta=ocm.Metadata(),
        component=ocm.Component(
            name=parsed.name,
            version=parsed.version,
            repositoryContexts=ocm_repos,
            provider=parsed.provider,
            componentReferences=[],
            sources=[],
            resources=[],
            labels=list(_iter_parsed_labels(labels=parsed.labels)),
            creationTime=now_ts,
        ),
        signatures=[],
    )

    dump(component_descriptor, parsed)


def append(parsed):
    raw = _parse_yaml_or_json(parsed.file)

    component = raw['component']

    attr = None
    if parsed.type in ('r', 'resource'):
        attr = component['resources']
        ocm_cls = ocm.Resource

    elif parsed.type in ('s', 'source'):
        attr = component['sources']
        ocm_cls = ocm.Source

    elif parsed.type in ('c', 'component-reference'):
        attr = component['componentReferences']
        ocm_cls = ocm.ComponentReference
    elif parsed.type in ('l', 'component-label'):
        attr = component['labels']
        ocm_cls = None

    labels = list(_iter_parsed_labels(labels=labels)) if (labels := parsed.labels) else []

    if ocm_cls is None:
        # special-case: component-labels
        attr.extend(labels)
    else:
        if _have_yaml:
            obj = yaml.safe_load(sys.stdin)
        else:
            obj = json.load(sys.stdin)

        obj = obj if isinstance(obj, list) else [obj]

        artefacts = [
            dacite.from_dict(
                data_class=ocm_cls,
                data=o,
                config=dacite.Config(
                    cast=(enum.Enum,),
                ),
            )
            for o in obj
        ]

        for artefact in artefacts:
            artefact.labels = list(artefact.labels) + labels

        attr: list
        attr.extend([dataclasses.asdict(a) for a in artefacts])

    with open(parsed.file, 'w') as f:
        if _have_yaml:
            yaml.dump(
                data=raw,
                stream=f,
                Dumper=ocm.EnumValueYamlDumper,
            )
        else:
            json.dump(raw, f)


def upload(parsed):
    if not _have_oci:
        print('ERROR: `oci`-package is not available - cannot upload')
        exit(1)

    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(),
    )
    component_descriptor = ocm.ComponentDescriptor.from_dict(_parse_yaml_or_json(parsed.file))
    component = component_descriptor.component
    if parsed.ocm_repo:
        component.repositoryContexts.append(
            ocm.OciOcmRepository(baseUrl=parsed.ocm_repo),
        )

    ocm_tgt_repo = component_descriptor.component.current_ocm_repo
    if not ocm_tgt_repo:
        print('ERROR: must define ocm-repository either via --ocm-repo, or via component-descriptor')
        exit(1)

    oci_target_ref = component.current_ocm_repo.component_version_oci_ref(component)

    for artefact in component.iter_artefacts():
        access = artefact.access
        if not access:
            continue

        if not access.type is ocm.AccessType.LOCAL_BLOB:
            continue

        # upload local-blobs, if needed
        if oci_client.head_blob(
            image_reference=oci_target_ref,
            digest=access.localReference,
            absent_ok=True,
        ):
            continue # no need to upload again
        blob_path_candidates = (
            os.path.join(
                parsed.blobs_dir,
                access.localReference.removeprefix('sha256:')
            ),
            os.path.join(
                parsed.blobs_dir,
                access.localReference,
            ),
        )
        for candidate in blob_path_candidates:
            if not os.path.exists(candidate):
                continue

        if not os.path.exists(candidate):
            print(f'error: did not find expected blob at {candidate=}')
            print(f'this blob was expected for {artefact=}')
            exit(1)
        with open(candidate, 'rb') as f:
            oci_client.put_blob(
                image_reference=oci_target_ref,
                digest=access.localReference,
                octets_count=access.size,
                data=f,
            )

    print(f'Uploading OCM Component-Descriptor to: {oci_target_ref=}')
    print(f'{parsed.on_exist=}')
    ocm.upload.upload_component_descriptor(
        component_descriptor=component_descriptor,
        oci_client=oci_client,
        on_exist=parsed.on_exist,
    )


def download(parsed):
    if not _have_oci:
        print('ERROR: `oci`-package is not available - cannot download')
        exit(1)

    cname, cversion = parsed.component.split(':')
    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(absent_ok=True),
    )

    ocm_repo = ocm.OciOcmRepository(
        baseUrl=parsed.ocm_repository,
    )

    def _fetch_component_descriptor(
        name: str,
        version: str,
        ocm_repo: ocm.OciOcmRepository | str | None=None,
    ) -> ocm.ComponentDescriptor:
        target_ref = ocm_repo.component_version_oci_ref(
            name=name,
            version=version
        )
        manifest = oci_client.manifest(
            image_reference=target_ref
        )
        try:
            cfg_blob = oci_client.blob(
                image_reference=target_ref,
                digest=manifest.config.digest
            )
            cfg_raw = json.loads(cfg_blob.text)
            cfg = dacite.from_dict(
                data_class=ocm.oci.ComponentDescriptorOciCfg,
                data=cfg_raw
            )
            layer_digest = cfg.componentDescriptorLayer.digest
            layer_mimetype = cfg.componentDescriptorLayer.mediaType
        except Exception as e:
            print(f'Failed to retrieve component-descriptor-cfg: {e=}, falling back to first layer')

            # by contract, the first layer must always be a tar w/ component-descriptor
            layer_digest = manifest.layers[0].digest
            layer_mimetype = manifest.layers[0].mediaType

        if not layer_mimetype in ocm.oci.component_descriptor_mimetypes:
            print(f'Error: {target_ref=} {layer_mimetype=} was unexpected')
            exit(1)

        raw = oci_client.blob(
            image_reference=target_ref,
            digest=layer_digest,
            stream=False, # manifests are typically small - do not bother w/ streaming
        ).content
        return ocm.oci.component_descriptor_from_tarfileobj(fileobj=io.BytesIO(raw))

    root_component_descriptor = _fetch_component_descriptor(
        name=cname,
        version=cversion,
        ocm_repo=ocm_repo
    )
    component = root_component_descriptor.component

    if parsed.outfile == '-':
        outfh = sys.stdout.buffer
    else:
        outfh = open(parsed.outfile, 'wb')

    if (t := parsed.type) in ('component-descriptor', 'c'):
        yaml.dump(
            data=dataclasses.asdict(root_component_descriptor),
            stream=outfh,
            encoding='utf-8',
            Dumper=ocm.EnumValueYamlDumper,
        )
        if parsed.recursive:
            for node in ocm.iter.iter(
                component=component,
                lookup=lambda cid, repo=None: _fetch_component_descriptor(
                    name=cid.name,
                    version=cid.version,
                    ocm_repo=(repo or ocm_repo)
                ),
                recursion_depth=-1,
                prune_unique=True,
                node_filter=ocm.iter.Filter.components,
                ocm_repo=ocm_repo,
            ):
                comp = node.component
                if comp.name == component.name and comp.version == component.version:
                    continue

                component_descriptor = _fetch_component_descriptor(
                    name=comp.name,
                    version=comp.version,
                    ocm_repo=ocm_repo
                )
                outfh.write(b'---\n')
                yaml.dump(
                    data=dataclasses.asdict(component_descriptor),
                    stream=outfh,
                    encoding='utf-8',
                    Dumper=ocm.EnumValueYamlDumper,
                )

        outfh.flush()
        if outfh is not sys.stdout.buffer:
            outfh.close()
        exit(0)

    elif t in ('resource', 'r'):
        artefacts = component.resources
    elif t in ('source', 's'):
        artefacts = component.sources
    else:
        raise ValueError(f'unexpected {parsed.type=} - this is a bug')

    artefact = None
    if len(artefacts) < 1:
        print(f'Error: {cname}:{cversion} has no artefacts of {parsed.type=}')
        exit(1)
    elif len(artefacts) == 1:
        artefact = artefacts[0]

    name = parsed.name
    id_attrs = {}
    for attrspec in parsed.artefact_ids:
        key, value = attrspec.split('=')
        id_attrs[key] = value

    have_id = bool(name or id_attrs)

    if not have_id and len(artefacts) > 1:
        print('Error: must specify artefact-id')
        exit(1)

    matches = 0
    if have_id:
        for a in artefacts:
            if name != a.name:
                continue

            for k,v in id_attrs.items():
                if hasattr(a, k):
                    if getattr(a, k) != v:
                        break
                else:
                    if not k in a.extraIdentity:
                        break
                    if a.extraIdentity[k] != v:
                        break
            else:
                # if there was no break, we have a candidate
                matches += 1
                artefact = a

    if not artefact:
        print(f'Error: did not find artefact {name=} {id_attrs=}')
        exit(1)

    if matches > 1:
        print(f'Error: artefact was not specified unambiguously: {name=} {id_attrs=}')
        exit(1)

    # at this point, we have one single, and unambiguously specified artefact
    access = artefact.access
    image_ref = None
    digest = None

    if access.type is ocm.AccessType.LOCAL_BLOB:
        image_ref = component.current_ocm_repo.component_version_oci_ref(
            name=cname,
            version=cversion,
        )
        digest = access.localReference

    elif access.type is ocm.AccessType.OCI_REGISTRY:
        if not artefact.type is ocm.ArtefactType.HELM_CHART:
            print(f'Error: {artefact.type=} not implemented for ociRegistry access')
            exit(1)

        image_ref = access.imageReference
        manifest = oci_client.manifest(image_reference=access.imageReference)

        target_mime = 'application/vnd.cncf.helm.chart.content.v1.tar+gzip'
        helm_chart_layer = None
        for layer in manifest.layers:
            if layer.mediaType == target_mime:
                helm_chart_layer = layer
                break
        if not helm_chart_layer:
            print(f'Error: expected Helm chart layer ({target_mime})')
            exit(1)

        digest = helm_chart_layer.digest

    else:
        print(f'Error: {access.type=} not implemented')
        exit(1)
    blob_rq = oci_client.blob(
        image_reference=image_ref,
        digest=digest,
    )

    for chunk in blob_rq.iter_content(chunk_size=4096):
        outfh.write(chunk)
    outfh.flush()


def replicate(parsed):
    ctt.__main__.replicate(parsed)


def _traverse(parsed):
    node_kinds = parsed.node_kinds

    if 'c' in node_kinds:
        components = True
    else:
        components = False

    if 'r' in node_kinds:
        resources = True
    else:
        resources = False

    if 's' in node_kinds:
        sources = True
    else:
        sources = False

    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(
            docker_cfg=parsed.docker_cfg,
        ),
    )

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(parsed.ocm_repo),
        oci_client=oci_client,
    )
    component = component_descriptor_lookup(parsed.name)

    traverse(
        component=component,
        components=components,
        sources=sources,
        resources=resources,
        component_descriptor_lookup=component_descriptor_lookup,
        print_expr=parsed.print_expr,
        filter_expr=parsed.filter_expr,
    )


def traverse(
    component: ocm.Component,
    components: bool,
    sources: bool,
    resources: bool,
    component_descriptor_lookup,
    print_expr: str=None,
    filter_expr: str=None,
):

    for node in ocm.iter.iter(
        component=component,
        lookup=component_descriptor_lookup,
    ):
        indent = len(node.path * 2)

        is_component_node = isinstance(node, ocm.iter.ComponentNode)
        is_source_node = isinstance(node, ocm.iter.SourceNode)
        is_resource_node = isinstance(node, ocm.iter.ResourceNode)

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

        if is_component_node:
            if not print_expr:
                prefix = 'c'
                print(f'{prefix}{" " * indent}{node.component.name}:{node.component.version}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': None})) # nosec B307
        if is_resource_node:
            if not print_expr:
                prefix = 'r'
                indent += 1
                print(f'{prefix}{" " * indent}{node.resource.name}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': node.resource})) # nosec B307
        if is_source_node:
            if not print_expr:
                prefix = 's'
                indent += 1
                print(f'{prefix}{" " * indent}{node.source.name}')
            else:
                print(eval(print_expr, {'node': node, 'artefact': node.source})) # nosec B307


def imagevector(parsed):
    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(
            docker_cfg=parsed.docker_cfg,
        ),
    )

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(parsed.ocm_repo),
        oci_client=oci_client,
    )
    root_component = component_descriptor_lookup(parsed.root_name).component

    if not parsed.recursive:
        if not parsed.name:
            print('Error: must pass --name if not using --recursive')
            exit(1)
        if ':' in parsed.name:
            component = component_descriptor_lookup(parsed.name)
        else:
            component_name = parsed.name
            for cnode in ocm.iter.iter(
                component=root_component,
                lookup=component_descriptor_lookup,
                node_filter=ocm.iter.Filter.components,
            ):
                if cnode.component.name == component_name:
                    component = cnode.component
                    break
            else:
                print(
                  f'Error: did not find version for {component_name=} in {root_component.name=}'
                )
                exit(1)
        components = (component,)
    else:
        def filter_expr(node):
            if not parsed.filter_expr:
                return True
            return eval( # nosec B307
                parsed.filter_expr, {
                    'node': node,
                    'component': node.component,
                })

        components = (
            node.component for node in
            ocm.iter.iter(
                component=root_component,
                lookup=component_descriptor_lookup,
                node_filter=ocm.iter.Filter.components,
            )
            if filter_expr(node)
        )

    images = []
    seen_names = set()

    for component in components:
        for image in ocm.gardener.iter_oci_image_dicts_from_rooted_component(
            component=component,
            root_component=root_component,
            component_descriptor_lookup=component_descriptor_lookup,
        ):
            name = image['name']
            if name in seen_names:
                continue
            seen_names.add(name)
            images.append(image)

    imagevector = ocm.gardener.as_image_vector(
        images=images,
    )

    print(yaml.safe_dump(imagevector))


def generate_config(parsed):
    simple_cfg_script_path = os.path.join(
        own_dir,
        '../ctt/simple-cfg',
    )

    # strip toplevel-command and delegate
    os.execv(simple_cfg_script_path, sys.argv[1:])


def main():
    parser = argparse.ArgumentParser()
    maincmd_parsers = parser.add_subparsers(
        title='commands',
        required=True,
    )

    create_parser = maincmd_parsers.add_parser(
        'create',
        aliases=('c',),
        help='creates a minimal OCM Component Descriptor',
    )
    create_parser.add_argument('--name', default=None)
    create_parser.add_argument('--version', default=None)
    create_parser.add_argument('--provider', default=None)
    create_parser.add_argument('--ocm-repo', default=None)
    create_parser.add_argument('--label', dest='labels', action='append', default=[])
    create_parser.add_argument('--out', '-o', default='-')
    create_parser.set_defaults(callable=create)

    add_parser = maincmd_parsers.add_parser(
        'append',
        aliases=('a',),
        help='appends resources, sources, or component-references to component-descriptor',
    )
    add_parser.add_argument(
        'type',
        choices=(
            'r', 'resource',
            's', 'source',
            'c', 'component-reference',
            'l', 'component-label',
        )
    )
    add_parser.add_argument('--file', '-f', required=True)
    add_parser.add_argument(
        '--label',
        dest='labels',
        action='append',
        default=[],
        help='labels to set for passed artefact (for convenience)',
    )
    add_parser.set_defaults(callable=append)

    upload_parser = maincmd_parsers.add_parser(
        'upload',
        aliases=('u',),
        help='uploads a component-descriptor to an OCI-Registry',
    )
    upload_parser.add_argument('--file', '-f', required=True)
    upload_parser.add_argument(
        '--blobs-dir',
        required=False,
        help='optional path to lookup local-blobs. fnames must equal sha256-hexdigest',
    )
    upload_parser.add_argument('--ocm-repo', default=None)
    upload_parser.add_argument(
        '--on-exist',
        type=ocm.upload.UploadMode,
        default=ocm.upload.UploadMode.SKIP,
    )
    upload_parser.set_defaults(callable=upload)

    download_parser = maincmd_parsers.add_parser(
        'download',
        aliases=('d',),
    )
    download_parser.add_argument(
        'type',
        choices=(
            'component-descriptor', 'c',
            'source', 's',
            'resource', 'r',
        ),
    )
    download_parser.add_argument(
        '--component', '-c',
        required=True,
        help='component: <component>:<version>',
    )
    download_parser.add_argument(
        '--ocm-repository', '-O',
        required=True,
    )
    download_parser.add_argument(
        '--name',
    )
    download_parser.add_argument(
        '--id',
        dest='artefact_ids',
        help='artefact-id - format: --id <attr-name>=<value>',
        action='append',
        default=[],
    )
    download_parser.add_argument(
        '--outfile', '-o',
        default='-',
    )
    download_parser.add_argument(
        '--recursive',
        action='store_true',
    )
    download_parser.set_defaults(callable=download)

    replicate_parser = maincmd_parsers.add_parser(
        'replicate',
        aliases=('r',),
    )
    replicate_parser.set_defaults(callable=replicate)
    ctt.__main__.configure_parser(replicate_parser)

    replicate_cfg_parser = maincmd_parsers.add_parser(
        'generate-replication-cfg',
        aliases=('simple-cfg',),
    )
    replicate_cfg_parser.set_defaults(callable=generate_config)
    replicate_cfg_parser.add_argument('params', nargs='*')

    traverse_parser = maincmd_parsers.add_parser(
        'traverse',
        aliases=('t',),
    )
    traverse_parser.set_defaults(callable=_traverse)
    traverse_parser.add_argument(
        '--name',
        help='OCM-Component-Name and Version (<name>:<version>)',
        required=True,
    )
    traverse_parser.add_argument(
        '--ocm-repo',
        required=True,
    )
    traverse_parser.add_argument(
        '--docker-cfg',
        required=False,
        help='path to dockerd\'s `config.json` file',
    )
    traverse_parser.add_argument(
        '--node-kinds',
        default='crs',
        help='''\
            which nodes to include: (c)omponents, (r)esources, (s)ources; applied before
            evaluating --filter-expr
            ''',
    )
    traverse_parser.add_argument(
        '--print-expr',
        help='a python-expression that will be used to print nodes',
    )
    traverse_parser.add_argument(
        '--filter-expr',
        help='a python-expression used to filter nodes (omit node if evaluates to False)'
    )

    imgvector_cfg_parser = maincmd_parsers.add_parser(
        'image-vector',
        aliases=('i',),
    )
    imgvector_cfg_parser.set_defaults(callable=imagevector)
    imgvector_cfg_parser.add_argument(
        '--name',
        help='OCM-Component-Version (<name>:<version>) (required if not using --recursive)',
        required=False,
    )
    imgvector_cfg_parser.add_argument(
        '--filter-expr',
        help=textwrap.dedent('''\
        a python-expression to filter componentversions (only used if --recursive)
        in scope: `component` (ocm.Component) and `node` (ocm.iter.ComponentNode)
        True: include componentversion
        False: exclude componentversion
        '''),
        required=False,
        default=None,
    )
    imgvector_cfg_parser.add_argument(
        '--recursive',
        action='store_true',
        default=False,
        help='if set, will yield imagevector for all subcomponents recursively',
    )
    imgvector_cfg_parser.add_argument(
        '--root-name',
        help='OCM-Root-Component-Name and Version (<name>:<version>)',
        required=True,
    )
    imgvector_cfg_parser.add_argument(
        '--ocm-repo',
        required=True,
    )
    imgvector_cfg_parser.add_argument(
        '--docker-cfg',
        required=False,
        help='path to dockerd\'s `config.json` file',
    )

    if len(sys.argv) < 2:
        parser.print_usage()
        exit(0)

    parsed = parser.parse_args()

    parsed.callable(parsed)


if __name__ == '__main__':
    main()
