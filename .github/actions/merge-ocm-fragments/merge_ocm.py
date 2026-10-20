# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import os

import yaml


def _artefact_identity(artefact: dict) -> tuple:
    '''Return a hashable identity key for an OCM artefact (resource or source).'''
    extra = artefact.get('extraIdentity') or {}
    return (
        artefact.get('name'),
        artefact.get('version'),
        tuple(sorted(extra.items())),
    )


def _read_attempt(fpath: str) -> int:
    '''
    Read the run-attempt from a {fpath}.meta sidecar file.
    Returns 0 if the sidecar is absent (fragments pre-dating the convention).
    '''
    meta_path = f'{fpath}.meta'
    if not os.path.isfile(meta_path):
        return 0
    with open(meta_path) as f:
        meta = yaml.safe_load(f)
    return int(meta.get('run-attempt', 0))


def merge_fragments(
    component_constructor: dict,
    fragments_dir: str,
) -> dict:
    '''
    Merges all `.ocm-artefacts` YAML files found in fragments_dir into component_constructor
    (modified in-place). Each fragment may contain `resources` and/or `sources` lists.

    After merging, versions are patched in for artefacts with `relation: local` that do not
    already carry a version, using the component-level version.

    When the same artefact identity (name, version, extraIdentity) appears in fragments from
    multiple run-attempts, the entry from the highest attempt number wins.  This handles the
    case where only a subset of jobs is re-run: successful jobs from earlier attempts and
    re-run jobs from later attempts coexist in the artefact store, and the latest attempt's
    output should take precedence.  The attempt number is read from a `{fragment}.meta`
    sidecar file written by export-ocm-fragments; fragments without a sidecar are treated
    as attempt 0.

    Consumed fragment and sidecar files are removed from fragments_dir.

    Returns the modified component_constructor.
    '''
    if 'sources' not in component_constructor:
        component_constructor['sources'] = []
    if 'resources' not in component_constructor:
        component_constructor['resources'] = []

    resource_attempt: dict[tuple, int] = {
        _artefact_identity(r): 0 for r in component_constructor['resources']
    }
    source_attempt: dict[tuple, int] = {
        _artefact_identity(s): 0 for s in component_constructor['sources']
    }

    for fname in os.listdir(fragments_dir):
        if not fname.endswith('.ocm-artefacts'):
            continue
        fpath = os.path.join(fragments_dir, fname)
        if not os.path.isfile(fpath):
            continue

        attempt = _read_attempt(fpath)
        print(f'adding artefacts from {fpath} (attempt={attempt})')
        with open(fpath) as f:
            artefacts = yaml.safe_load(f)

        for resource in (artefacts.get('resources') or []):
            key = _artefact_identity(resource)
            prev = resource_attempt.get(key, -1)
            if attempt < prev:
                print(f'  skipping resource {key}: attempt {attempt} < {prev}')
                continue
            if attempt == prev:
                print(f'  skipping duplicate resource {key} (same attempt)')
                continue
            resource_attempt[key] = attempt
            for i, r in enumerate(component_constructor['resources']):
                if _artefact_identity(r) == key:
                    print(f'  replacing resource {key}: attempt {prev} -> {attempt}')
                    component_constructor['resources'][i] = resource
                    break
            else:
                component_constructor['resources'].append(resource)

        for source in (artefacts.get('sources') or []):
            key = _artefact_identity(source)
            prev = source_attempt.get(key, -1)
            if attempt < prev:
                print(f'  skipping source {key}: attempt {attempt} < {prev}')
                continue
            if attempt == prev:
                print(f'  skipping duplicate source {key} (same attempt)')
                continue
            source_attempt[key] = attempt
            for i, s in enumerate(component_constructor['sources']):
                if _artefact_identity(s) == key:
                    print(f'  replacing source {key}: attempt {prev} -> {attempt}')
                    component_constructor['sources'][i] = source
                    break
            else:
                component_constructor['sources'].append(source)

        os.unlink(fpath)
        meta_path = f'{fpath}.meta'
        if os.path.isfile(meta_path):
            os.unlink(meta_path)

    cversion = component_constructor.get('version')
    for artefact in component_constructor['sources'] + component_constructor['resources']:
        if not cversion:
            continue
        if artefact.get('version'):
            continue
        if artefact.get('relation') != 'local':
            continue
        artefact['version'] = cversion

    return component_constructor
