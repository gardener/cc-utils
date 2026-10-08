# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import copy


def _clean_label(label: dict) -> dict:
    label = copy.deepcopy(label)
    label.pop('signing', None)
    label.pop('version', None)
    return label


def _clean_labels(labels: list) -> list:
    return [_clean_label(l) for l in (labels or [])]


def _clean_access(access: dict) -> dict:
    if not access:
        return access
    access = copy.deepcopy(access)
    # component-descriptor uses 'github', constructor expects 'gitHub'
    if access.get('type') == 'github':
        access['type'] = 'gitHub'
    return access


def _clean_source(source: dict) -> dict:
    source = copy.deepcopy(source)
    source.pop('extraIdentity', None)
    source['labels'] = _clean_labels(source.get('labels'))
    if 'access' in source:
        source['access'] = _clean_access(source['access'])
    return source


def _clean_resource(resource: dict) -> dict:
    resource = copy.deepcopy(resource)
    resource.pop('extraIdentity', None)
    resource.pop('digest', None)
    resource.pop('srcRefs', None)
    resource['labels'] = _clean_labels(resource.get('labels'))
    if 'access' in resource:
        resource['access'] = _clean_access(resource['access'])
    return resource


def _clean_component_reference(ref: dict) -> dict:
    ref = copy.deepcopy(ref)
    ref.pop('extraIdentity', None)
    ref.pop('digest', None)
    ref['labels'] = _clean_labels(ref.get('labels'))
    return ref


def to_constructor(component_descriptor: dict) -> dict:
    '''
    Transform a component-descriptor dict into a single-component OCM constructor dict
    suitable for `ocm add cv`.

    Accepts both:
    - component-descriptor format (with meta/component wrapper, schemaVersion v2)
    - base-component format (component fields at top level, with componentPrefixes/main_source)
    '''
    if 'component' in component_descriptor:
        component = copy.deepcopy(component_descriptor['component'])
    else:
        component = copy.deepcopy(component_descriptor)
        component.pop('componentPrefixes', None)
        component.pop('main_source', None)

    # provider is a plain string in the descriptor, constructor expects {name: ...}
    provider = component.get('provider')
    if isinstance(provider, str):
        component['provider'] = {'name': provider}

    component.pop('repositoryContexts', None)
    component['labels'] = _clean_labels(component.get('labels'))

    component['sources'] = [
        _clean_source(s) for s in (component.get('sources') or [])
    ]
    component['resources'] = [
        _clean_resource(r) for r in (component.get('resources') or [])
    ]
    component['componentReferences'] = [
        _clean_component_reference(r) for r in (component.get('componentReferences') or [])
    ]

    return component
