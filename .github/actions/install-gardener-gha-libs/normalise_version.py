#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

'''
Normalise a cc-utils VERSION to PEP-440 and write it back to all version files.

Prints the (possibly normalised) version to stdout so callers can capture it.

Environment variables:
  CC_UTILS_ROOT  root of the cc-utils checkout (defaults to cwd)
'''

import os
import re


_VERSION_FILES = (
    'VERSION',
    'ci/VERSION',
    'oci/VERSION',
    'ocm/VERSION',
    'cli/gardener_ci/VERSION',
)


def read_version(root: str) -> str:
    with open(os.path.join(root, 'VERSION')) as f:
        return f.read().strip()


def write_version(root: str, version: str):
    for rel in _VERSION_FILES:
        with open(os.path.join(root, rel), 'w') as f:
            f.write(version + '\n')


def normalise_version(version: str) -> str:
    '''
    Convert a non-final semver-like version to a PEP-440 dev release.

    Examples:
        1.2.3          -> 1.2.3          (unchanged, final)
        1.2.3-dev      -> 1.2.3.dev0
        1.2.3-rc.1     -> 1.2.3.dev0
        1.2.3-foo-bar  -> 1.2.3.dev0
    '''
    match = re.match(r'^(\d+\.\d+\.\d+)-', version)
    if match:
        return f'{match.group(1)}.dev0'
    return version


if __name__ == '__main__':
    root = os.environ.get('CC_UTILS_ROOT', os.getcwd())
    version = read_version(root)
    if '-' in version:
        version = normalise_version(version)
        write_version(root, version)
    print(version)
