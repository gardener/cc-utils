#!/usr/bin/env python

import argparse
import dataclasses
import pprint

import yaml

import ocm
import ocm.gardener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument(
        '--component-prefixes',
        action='append',
        default=[
            'europe-docker.pkg.dev/gardener-project/releases/gardener',
            'europe-docker.pkg.dev/gardener-project/snapshots/gardener',
        ],
    )
    parser.add_argument(
        '--format',
        default='yaml',
        choices=('yaml', 'pretty',),
    )

    parsed = parser.parse_args()

    component = ocm.Component(
        name='dummy',
        version='0.1.0',
        repositoryContexts=[],
        provider='ACME',
        sources=[],
        componentReferences=[],
        resources=[],
        labels=[],
    )

    imagevector = ocm.gardener.find_imagevector_file(
        repo_root=parsed.repo,
    )

    if not imagevector:
        print('Warning: did not find an imagevector')
        exit(0)

    print(f'{imagevector=}')

    images = ocm.gardener.iter_images_from_imagevector(imagevector)

    ocm.gardener.add_resources_from_imagevector(
        component=component,
        images=images,
        component_prefixes=parsed.component_prefixes,
    )

    if parsed.format == 'pretty':
        pprint.pprint(component)
    elif parsed.format == 'yaml':
        print(
            yaml.dump(
                dataclasses.asdict(component),
                Dumper=ocm.EnumValueYamlDumper,
            )
        )


if __name__ == '__main__':
    main()
