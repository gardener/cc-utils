# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from copy import deepcopy
import collections
import github3.exceptions
import functools
import itertools
import semver
import typing
import yaml

import version
from github.util import GitHubRepositoryHelper, github_api_ctor
from util import not_none, check_type, FluentIterable
from .model import (
    COMPONENT_DESCRIPTOR_ASSET_NAME,
    Component,
    ComponentReference,
    ContainerImage,
    DependencyBase,
    Product,
)


class ComponentResolutionException(Exception):
    def __init__(self, msg, component_reference):
        self.msg = msg
        self.component_reference = component_reference

    def __str__(self):
        return 'error resolving {cr}: {msg}'.format(
            cr=self.component_reference,
            msg=self.msg,
        )


class ResolverBase(object):
    def __init__(
        self,
        cfg_factory=None,
    ):
        self.cfg_factory=cfg_factory

    @functools.lru_cache()
    def _github_cfg_for_hostname(self, host_name):
        not_none(host_name)
        for github_cfg in self.cfg_factory._cfg_elements(cfg_type_name='github'):
            if github_cfg.matches_hostname(host_name=host_name):
                return github_cfg
        raise RuntimeError('no github_cfg for {h}'.format(host_name))

    @functools.lru_cache()
    def _github_api_for_hostname(self, host_name):
        not_none(host_name)
        # hard-code schema to https
        url = 'https://' + host_name
        ctor = github_api_ctor(github_url=url)
        return ctor()

    def _repository_helper(self, component_reference):
        if isinstance(component_reference, tuple):
            name, version = component_reference
            component_reference = ComponentReference.create(name=name, version=version)

        gh_helper_ctor = functools.partial(
                GitHubRepositoryHelper,
                owner=component_reference.github_organisation(),
                name=component_reference.github_repo(),
        )

        if self.cfg_factory:
            return gh_helper_ctor(
                github_cfg=self._github_cfg_for_hostname(
                    host_name=component_reference.github_host(),
                )
            )
        else:
            return gh_helper_ctor(
                github_api=self._github_api_for_hostname(
                    host_name=component_reference.github_host(),
                )
            )


class ComponentDescriptorResolver(ResolverBase):
    def retrieve_raw_descriptor(self, component_reference, as_dict=False):
        if isinstance(component_reference, tuple):
            name, version = component_reference
            component_reference = ComponentReference.create(name=name, version=version)

        repo_helper = self._repository_helper(component_reference)
        dependency_descriptor = repo_helper.retrieve_asset_contents(
                release_tag=component_reference.version(),
                asset_label=COMPONENT_DESCRIPTOR_ASSET_NAME,
        )
        if as_dict:
            return yaml.load(dependency_descriptor, Loader=yaml.SafeLoader)
        else:
            return dependency_descriptor

    def retrieve_descriptor(self, component_reference):
        try:
            dependency_descriptor = self.retrieve_raw_descriptor(
                component_reference=component_reference,
                as_dict=True,
            )
        except github3.exceptions.NotFoundError as nfe:
            raise ComponentResolutionException(
                msg=nfe.msg,
                component_reference=component_reference,
            )

        return Product.from_dict(dependency_descriptor)

    def resolve_component_references(
        self,
        product,
    ):
        def unresolved_references(component):
            component_references = component.dependencies().components()
            yield from filter(lambda cr: not product.component(cr), component_references)

        merged = Product.from_dict(deepcopy(dict(product.raw.items())))

        for component_reference in itertools.chain(
            *map(unresolved_references, product.components())
        ):
            resolved_descriptor = self.retrieve_descriptor(component_reference)
            merged = merge_products(merged, resolved_descriptor)

        return merged


class ComponentResolver(ResolverBase):
    def latest_component_version(self, component_name: str):
        component_reference = ComponentReference.create(name=component_name, version=None)
        repo_helper = self._repository_helper(component_reference)
        latest_version = version.find_latest_version(repo_helper.release_versions())
        return latest_version

    def greatest_release_before(self, component_name: str, version: str):
        component_reference = ComponentReference.create(name=component_name, version=version)
        repo_helper = self._repository_helper(component_reference)
        version = semver.parse_version_info(version)

        versions = sorted(repo_helper.release_versions()) # greatest version comes last
        versions = [v for v in versions if v < version]

        if len(versions) == 0:
            return None # no release before current was found
        return versions[-1]


def merge_products(left_product, right_product):
    not_none(left_product)
    not_none(right_product)

    # start with a copy of left_product
    merged = Product.from_dict(deepcopy(dict(left_product.raw.items())))
    for component in right_product.components():
        existing_component = merged.component(component)
        if existing_component:
            # it is acceptable to add an existing component iff it is identical
            if existing_component.raw == component.raw:
                continue # skip
            else:
                raise ValueError(
                    'conflicting component definitions: {c1}, {c2}'.format(
                        c1=':'.join((existing_component.name(), existing_component.version())),
                        c2=':'.join((component.name(), component.version())),
                    )
                )
        merged.add_component(component)

    return merged


def diff_products(left_product, right_product, ignore_component_names=()):
    # only take component references into account for now and assume
    # that component versions are always identical content-wise
    left_components = {
        c for c in left_product.components() if c.name() not in ignore_component_names
    }
    right_components = {
        c for c in right_product.components() if c.name() not in ignore_component_names
    }

    return diff_components(
        left_components=left_components,
        right_components=right_components,
        ignore_component_names=ignore_component_names,
    )


ComponentDiff = collections.namedtuple(
    'ComponentDiff',
    [
        'crefs_only_left',
        'crefs_only_right',
        'crefpairs_version_changed',
        'names_only_left',
        'names_only_right',
        'names_version_changed',
    ]
)


def diff_components(left_components, right_components, ignore_component_names=()) -> ComponentDiff:
    left_components = set(left_components)
    right_components = set(right_components)

    if left_components == right_components:
        return None # no diff

    components_only_left = left_components - right_components
    components_only_right = right_components - left_components

    def find_changed_component(changed_component, components):
        for c in components:
            if c.name() == changed_component.name():
                return (changed_component, c)
        return (changed_component, None) # no pair component found

    components_with_changed_versions = FluentIterable(items=components_only_left) \
        .map(functools.partial(find_changed_component, components=right_components)) \
        .filter(lambda cs: cs[1] is not None) \
        .as_list()
    # pairs of crefs (left-version:right-version)

    left_names = set(map(lambda c: c.name(), components_only_left))
    right_names = set(map(lambda c: c.name(), components_only_right))
    names_version_changed = set(map(lambda cp: cp[0].name(), components_with_changed_versions))

    both_names = left_names & right_names
    left_names -= both_names
    right_names -= both_names

    return ComponentDiff(
        crefs_only_left=components_only_left,
        crefs_only_right=components_only_right,
        crefpairs_version_changed=set(components_with_changed_versions),
        names_only_left=left_names,
        names_only_right=right_names,
        names_version_changed=names_version_changed,
    )


def greatest_references(references: typing.Iterable[DependencyBase]):
    '''
    yields the component references from the specified iterable of ComponentReference that
    have the greates version (grouped by component name).
    Id est: if the sequence contains exactly one version of each contained component name,
    the sequence is returned unchanged.
    '''
    not_none(references)
    references = list(references)
    for ref in references:
        check_type(ref, DependencyBase)

    names = [
        ref.name() for ref
        in references
    ]

    for name in names:
        matching_refs = [r for r in references if r.name() == name]
        if len(matching_refs) == 1:
            # in case reference name was unique, do not bother sorting
            # (this also works around issues from non-semver versions)
            yield matching_refs[0]
            continue

        # there might be multiple component versions of the same name
        # --> use the greatest version in that case
        matching_refs = sorted(
            matching_refs,
            key=lambda r: semver.parse_version_info(r.version()),
        )
        # greates version comes last
        yield matching_refs[-1]


def _enumerate_images(
    component_descriptor: Product,
    image_reference_filter=lambda _: True,
) -> typing.Iterable[typing.Tuple[Component, ContainerImage]]:
    for component in component_descriptor.components():
        component_dependencies = component.dependencies()
        for container_image in filter(
                image_reference_filter,
                component_dependencies.container_images()
        ):
            yield (component, container_image)
