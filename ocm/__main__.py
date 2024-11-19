import argparse
import dataclasses
import datetime
import json
import os
import sys

import ocm
import ocm.upload


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

    labels = []
    for label in parsed.labels:
        if os.path.exists(label):
            label = _parse_yaml_or_json(label)
        else:
            label = _yaml_or_json_load(label)

        label = ocm.Label(
            name=label['name'],
            value=label['value'],
        )
        labels.append(label)

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
            labels=labels,
            creationTime=now_ts,
        ),
        signatures=[],
    )

    dump(component_descriptor, parsed)


def append(parsed):
    raw = _parse_yaml_or_json(parsed.file)

    component = raw['component']

    if parsed.type in ('r', 'resource'):
        attr = component['resources']
    elif parsed.type in ('s', 'source'):
        attr = component['sources']

    if _have_yaml:
        obj = yaml.safe_load(sys.stdin)
    else:
        obj = json.load(sys.stdin)

    if isinstance(obj, list):
        attr.extend(obj)
    else:
        attr.append(obj)

    with open(parsed.file, 'w') as f:
        if _have_yaml:
            yaml.safe_dump(raw, f)
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
        blob_path = os.path.join(
            parsed.blobs_dir,
            access.localReference.removeprefix('sha256:')
        )
        if not os.path.exists(blob_path):
            print(f'error: did not find expected blob at {blob_path=}')
            print(f'this blob was expected for {artefact=}')
            exit(1)
        with open(blob_path, 'rb') as f:
            oci_client.put_blob(
                image_reference=oci_target_ref,
                digest=access.localReference,
                octets_count=access.size,
                data=f,
            )

    print(f'Uploading OCM Component-Descriptor to: {oci_target_ref=}')
    ocm.upload.upload_component_descriptor(
        component_descriptor=component_descriptor,
        oci_client=oci_client,
    )


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
        help='appends resources or sources to component-descriptor',
    )
    add_parser.add_argument('type', choices=('r', 'resource', 's', 'source'))
    add_parser.add_argument('--file', '-f', required=True)
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
    upload_parser.set_defaults(callable=upload)

    if len(sys.argv) < 2:
        parser.print_usage()
        exit(0)

    parsed = parser.parse_args()

    parsed.callable(parsed)


if __name__ == '__main__':
    main()
