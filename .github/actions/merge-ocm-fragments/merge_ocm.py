# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os

import yaml


def merge_fragments(
    component_descriptor: dict,
    fragments_dir: str,
) -> dict:
    '''
    Merges all `.ocm-artefacts` YAML files found in fragments_dir into component_descriptor
    (modified in-place). Each fragment may contain `resources` and/or `sources` lists.

    After merging, versions are patched in for artefacts with `relation: local` that do not
    already carry a version, using the component-level version.

    Consumed fragment files are removed from fragments_dir.

    Returns the modified component_descriptor.
    '''
    component = component_descriptor['component']
    if 'sources' not in component:
        component['sources'] = []
    if 'resources' not in component:
        component['resources'] = []

    for fname in os.listdir(fragments_dir):
        if not fname.endswith('.ocm-artefacts'):
            continue
        fpath = os.path.join(fragments_dir, fname)
        if not os.path.isfile(fpath):
            continue

        print(f'adding artefacts from {fpath}')
        with open(fpath) as f:
            artefacts = yaml.safe_load(f)

        if (resources := artefacts.get('resources')):
            component['resources'].extend(resources)
        if (sources := artefacts.get('sources')):
            component['sources'].extend(sources)

        os.unlink(fpath)

    cversion = component.get('version')
    for artefact in component['sources'] + component['resources']:
        if not cversion:
            continue
        if artefact.get('version'):
            continue
        if artefact.get('relation') != 'local':
            continue
        artefact['version'] = cversion

    return component_descriptor
