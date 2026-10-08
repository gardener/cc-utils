#!/usr/bin/env python3
# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import argparse
import sys
import tarfile

import yaml

import transform


def main():
    parser = argparse.ArgumentParser(
        description='Transform a component-descriptor.yaml into an OCM component constructor YAML',
    )
    parser.add_argument(
        'input',
        nargs='?',
        default='component-descriptor.yaml',
        help='path to component-descriptor.yaml, or a component-descriptor.tar.gz (default: component-descriptor.yaml)',
    )
    parser.add_argument(
        '-o', '--output',
        default='-',
        help='output path (default: stdout)',
    )
    args = parser.parse_args()

    if args.input.endswith('.tar.gz') or args.input.endswith('.tgz'):
        with tarfile.open(args.input) as tf:
            with tf.extractfile('component-descriptor.yaml') as f:
                component_descriptor = yaml.safe_load(f)
    else:
        with open(args.input) as f:
            component_descriptor = yaml.safe_load(f)

    constructor = transform.to_constructor(component_descriptor)

    output = yaml.safe_dump(constructor, default_flow_style=False, allow_unicode=True)

    if args.output == '-':
        sys.stdout.write(output)
    else:
        with open(args.output, 'w') as f:
            f.write(output)


if __name__ == '__main__':
    main()
