# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import dataclasses
import github3.exceptions
import github3.repos
import functools
import itertools
import typing
import yaml

import ccc.github
from ci.util import not_none, FluentIterable
from .model import (
    COMPONENT_DESCRIPTOR_ASSET_NAME,
    Component,
    ComponentReference,
    ContainerImage,
    ComponentDescriptor,
)
import product.v2

import gci.componentmodel as cm


class ComponentResolutionException(Exception):
    def __init__(self, msg, component_reference):
        self.msg = msg
        self.component_reference = component_reference

    def __str__(self):
        return 'error resolving {cr}: {msg}'.format(
            cr=self.component_reference,
            msg=self.msg,
        )


class ResolverBase:
    def __init__(
        self,
        cfg_factory=None,
    ):
        self.cfg_factory = cfg_factory

    def _repository_helper(self, component_reference):
        # late import due to circular dependency
        from github.util import GitHubRepositoryHelper

        if isinstance(component_reference, tuple):
            name, version = component_reference
            component_reference = ComponentReference.create(name=name, version=version)

        gh_helper_ctor = functools.partial(
                GitHubRepositoryHelper,
                owner=component_reference.github_organisation(),
                name=component_reference.github_repo(),
        )

        github_cfg = ccc.github.github_cfg_for_hostname(component_reference.github_host())
        github_api = ccc.github.github_api(github_cfg=github_cfg)

        return gh_helper_ctor(github_api=github_api)


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

        return ComponentDescriptor.from_dict(dependency_descriptor)

    def resolve_component_references(
        self,
        product,
    ):
        def unresolved_references(component):
            component_references = component.dependencies().components()
            yield from filter(lambda cr: not product.component(cr), component_references)

        merged = ComponentDescriptor.from_dict(deepcopy(dict(product.raw.items())))

        for component_reference in itertools.chain(
            *map(unresolved_references, product.components())
        ):
            resolved_descriptor = self.retrieve_descriptor(component_reference)
            merged = merge_products(merged, resolved_descriptor)

        return merged


def merge_products(left_product, right_product):
    not_none(left_product)
    not_none(right_product)

    # start with a copy of left_product
    merged = ComponentDescriptor.from_dict(deepcopy(dict(left_product.raw.items())))
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

    # merge overwrites
    for component_overwrite in right_product.component_overwrites():
        # only one overwrite per component is allowed
        for co in left_product.component_overwrites():
            if co.declaring_component == component_overwrite.declaring_component():
                raise ValueError(f'overwrite redefinition: {co}')
        merged._add_component_overwrite(component_overwrite=component_overwrite)

    return merged


@dataclasses.dataclass
class ComponentDiff:
    cidentities_only_left: set = dataclasses.field(default_factory=set)
    cidentities_only_right: set = dataclasses.field(default_factory=set)
    cpairs_version_changed: list = dataclasses.field(default_factory=list)
    # only set when new component is added/removed
    names_only_left: set = dataclasses.field(default_factory=set)
    names_only_right: set = dataclasses.field(default_factory=set)
    # only set on update
    names_version_changed: set = dataclasses.field(default_factory=set)


# for v2 =  pass cd v2 and resolve components
# make use of caching
def diff_products(
    left_product: cm.ComponentDescriptor,
    right_product: cm.ComponentDescriptor,
    ignore_component_names=(),
    cache_dir=None,
) -> ComponentDiff:
    if type(left_product) is not cm.ComponentDescriptor:
        raise NotImplementedError(
            f'left product unsupported type {type(left_product)=}.'
            ' Only ComponentDesciptorV2 is supported',
        )
    if type(right_product) is not cm.ComponentDescriptor:
        raise NotImplementedError(
            f'unsupported type {type(right_product)=}.'
            ' Only ComponentDesciptorV2 is supported',
        )
    # only take component references into account for now and assume
    # that component versions are always identical content-wise
    left_components: typing.Generator[cm.Component] = product.v2.components(
        component_descriptor_v2=left_product,
        cache_dir=cache_dir,
    )
    right_components: typing.Generator[cm.Component] = product.v2.components(
        component_descriptor_v2=right_product,
        cache_dir=cache_dir,
    )
    left_components = tuple(
        c for c in left_components if c.name not in ignore_component_names
    )
    right_components = tuple(
        c for c in right_components if c.name not in ignore_component_names
    )

    return diff_components(
        left_components=left_components,
        right_components=right_components,
        ignore_component_names=ignore_component_names,
    )


def diff_components(
    left_components: typing.Tuple[cm.Component],
    right_components: typing.Tuple[cm.Component],
    ignore_component_names=(),
) -> ComponentDiff:
    left_identities = {
        c.identity() for c in left_components if c.name not in ignore_component_names
    }
    right_identities = {
        c.identity() for c in right_components if c.name not in ignore_component_names
    }

    left_only_identities = left_identities - right_identities
    right_only_identities = right_identities - left_identities

    if left_only_identities == right_only_identities:
        return None # no diff

    left_components = tuple((
        c for c in left_components if c.identity() in left_only_identities
    ))
    right_components = tuple((
        c for c in right_components if c.identity() in right_only_identities
    ))

    def find_changed_component(
        changed_component: cm.Component,
        components: typing.List[cm.Component],
    ):
        for c in components:
            if c.name == changed_component.name:
                return (changed_component, c)
        return (changed_component, None) # no pair component found

    components_with_changed_versions = FluentIterable(items=left_components) \
        .map(functools.partial(find_changed_component, components=right_components)) \
        .filter(lambda cs: cs[1] is not None) \
        .as_list()
    # pairs of components (left:right-version)

    left_names = {i.name for i in left_identities}
    right_names = {i.name for i in right_identities}
    names_version_changed = {c[0].name for c in components_with_changed_versions}

    both_names = left_names & right_names
    left_names -= both_names
    right_names -= both_names

    return ComponentDiff(
        cidentities_only_left=left_only_identities,
        cidentities_only_right=right_only_identities,
        cpairs_version_changed=components_with_changed_versions,
        names_only_left=left_names,
        names_only_right=right_names,
        names_version_changed=names_version_changed,
    )


@dataclasses.dataclass
class ResourceDiff:
    left_component: cm.Component
    right_component: cm.Component
    resource_refs_only_left: typing.List[cm.Resource] = dataclasses.field(default_factory=list)
    resource_refs_only_right: typing.List[cm.Resource] = dataclasses.field(default_factory=list)
    resourcepairs_version_changed: typing.List[typing.Tuple[cm.Resource, cm.Resource]] = dataclasses.field(default_factory=list) # noqa:E501


def _add_if_not_duplicate(list, res):
    if res.name not in list:
        list.append(res)


def diff_resources(
    left_component: cm.Component,
    right_component: cm.Component,
) -> ResourceDiff:
    if type(left_component) is not cm.Component:
        raise NotImplementedError(
            f'unsupported {type(left_component)=}. Only cm.Component is supported',
        )
    if type(right_component) is not cm.Component:
        raise NotImplementedError(
            f'unsupported {type(right_component)=}. Only cm.Component is supported',
        )

    left_resource_identities = {
        r.identity(left_component.resources + right_component.resources): r
        for r in left_component.resources
    }
    right_resource_identities_to_resource = {
        r.identity(left_component.resources + right_component.resources): r
        for r in right_component.resources
    }

    resource_diff = ResourceDiff(
        left_component=left_component,
        right_component=right_component,
    )

    if left_resource_identities.keys() == right_resource_identities_to_resource.keys():
        return resource_diff

    left_names_to_resource = {r.name: r for r in left_component.resources}
    right_names_to_resource = {r.name: r for r in right_component.resources}
    # get left exclisive resources
    for resource in left_resource_identities.values():
        if not resource.name in right_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_left, resource)

    # get right exclusive images
    for resource in right_resource_identities_to_resource.values():
        if not resource.name in left_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_right, resource)

    def enumerate_group_pairs(
        left_resources: typing.List[cm.Resource],
        right_resources: typing.List[cm.Resource]
    ) -> typing.Tuple[typing.List[cm.Resource], typing.List[cm.Resource]]:
        # group the images with the same name on both sides
        for key in left_names_to_resource.keys():
            left_resource_group = [r for r in left_resources if r.name == key]
            right_resource_group = [r for r in right_resources if r.name == key]

            # key is always in left group
            if len(right_resource_group) == 0:
                continue
            else:
                yield (left_resource_group, right_resource_group)

    for left_resource_group, right_resource_group in enumerate_group_pairs(
        left_resources=left_component.resources,
        right_resources=right_component.resources,
    ):
        if len(left_resource_group) == 1 and len(right_resource_group) == 1:
            # if versions are equal resource will be ignored, resource is unchanged
            if left_resource_group[0].version != right_resource_group[0].version:
                resource_diff.resourcepairs_version_changed.append(
                    (left_resource_group[0], right_resource_group[0]),
                )
            continue

        left_identities = {
            r.identity(left_component.resources + right_component.resources): r
            for r in left_resource_group
        }
        right_identities = {
            r.identity(left_component.resources + right_component.resources): r
                            for r in right_resource_group
        }

        left_resource_ids = sorted(left_identities.keys())
        right_resource_ids = sorted(right_identities.keys())

        left_resources = [left_identities.get(id) for id in left_resource_ids]
        right_resources = [right_identities.get(id) for id in right_resource_ids]

        # remove all images present in both
        versions_in_both = {
            r.version for r in left_resources
        } & {
            r.version for r in right_resources
        }
        left_resources = [
            i for i in left_resources
            if not i.version in versions_in_both
        ]
        right_resources = [
            i for i in right_resources
            if not i.version in versions_in_both
        ]

        i = 0
        for i, left_resource in enumerate(left_resources):
            if i >= len(right_resources):
                _add_if_not_duplicate(resource_diff.resource_refs_only_left, left_resource)

            else:
                right_resource = right_resources[i]
                resource_diff.resourcepairs_version_changed.append((left_resource, right_resource))

        # returns an empyt dict if index out of bounds
        left_resource = left_resources[i:]
        right_resource = right_resources[i:]

        for i in left_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_left, i)

        for i in right_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_right, i)

    return resource_diff


def _dep_overwrites_for_component(
    component_descriptor,
    component,
):
    for comp_overwrites in component_descriptor.component_overwrites():
        dep_overwrites = comp_overwrites.dependency_overwrite(referenced_component=component)
        if dep_overwrites:
            yield dep_overwrites


def _effective_images(
    component_descriptor,
    component,
):
    dep_overwrites = tuple(
        _dep_overwrites_for_component(
            component_descriptor=component_descriptor,
            component=component,
        )
    )
    for image in component.dependencies().container_images():
        effective_image = None
        for do in dep_overwrites:
            # last wins (if any)
            effective_image = do.container_image(name=image.name(), version=image.version()) \
                    or effective_image
        yield effective_image or image


def _grouped_effective_images(
    *components,
    component_descriptor,
):
    '''
    returns a generator yielding sets of effective images for the given components, grouped by
    common image name. If the given component declares dependencies to multiples images of the
    same image name (but with different image versions), they will be grouped in a common set.
    Otherwise, the returned sets will contain exactly one element each.

    '''
    image_groups = collections.defaultdict(set) # image_name: [images]
    for component in components:
        for image in _effective_images(
                component_descriptor=component_descriptor,
                component=component
        ):
            image_groups[image.name()].add(image)

    for image_name, images in image_groups.items():
        yield images


def _enumerate_effective_images(
    component_descriptor: ComponentDescriptor,
    image_reference_filter=lambda _: True,
) -> typing.Iterable[typing.Tuple[Component, ContainerImage]]:
    for component in component_descriptor.components():
        for effective_image in _effective_images(component_descriptor, component):
            if image_reference_filter(effective_image):
                yield (component, effective_image)


class RefGuessingFailedError(Exception):
    pass


def guess_commit_from_source(
    artifact_name: str,
    github_repo: github3.repos.repo.Repository,
    ref: str,
    commit_hash: str=None,
):
    def in_repo(commit_ish):
        try:
            return github_repo.ref(commit_ish).object.sha
        except github3.exceptions.NotFoundError:
            pass

        try:
            return github_repo.commit(commit_ish).sha
        except (github3.exceptions.UnprocessableEntity, github3.exceptions.NotFoundError):
            return None

    # first guess: look for commit hash if defined
    if commit_hash:
        commit = in_repo(commit_hash)
        if commit:
            return commit

    # second guess: check for ref like 'refs/heads/main'
    if ref.startswith('refs/'):
        gh_ref = ref[len('refs/'):] # trim 'refs/' because of github3 api
        commit = in_repo(gh_ref)
        if commit:
            return commit
    else:
        commit = in_repo(ref)
        if commit:
            return commit

    # third guess: branch
    try:
        return github_repo.branch(ref).commit.sha
    except github3.exceptions.NotFoundError:
        pass

    # still unknown commit-ish throw error
    raise RefGuessingFailedError(
        f'failed to guess on ref for {artifact_name=} with {ref=}'
    )
