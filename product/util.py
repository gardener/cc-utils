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
import functools
import itertools
import typing
import yaml

import ccc.github
import version
from github.util import GitHubRepositoryHelper
from ci.util import not_none, check_type, FluentIterable
from .model import (
    COMPONENT_DESCRIPTOR_ASSET_NAME,
    Component,
    ComponentReference,
    ContainerImage,
    DependencyBase,
    ComponentDescriptor,
)
import version as ver


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
        self.cfg_factory = cfg_factory

    def _repository_helper(self, component_reference):
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


class ComponentResolver(ResolverBase):
    def latest_component_version(self, component_name: str):
        component_reference = ComponentReference.create(name=component_name, version=None)
        repo_helper = self._repository_helper(component_reference)
        latest_version = version.find_latest_version(repo_helper.release_versions())
        if not latest_version:
            raise ValueError(
                f'Component {component_name} has no valid release'
            )
        return latest_version

    def greatest_component_version_with_matching_minor(
            self,
            component_name: str,
            reference_version: str,
        ):
        component_reference = ComponentReference.create(name=component_name, version=None)
        repo_helper = self._repository_helper(component_reference)
        latest_version = version.find_latest_version_with_matching_minor(
                reference_version=ver.parse_to_semver(reference_version),
                versions=repo_helper.release_versions(),
        )
        if not latest_version:
            raise ValueError(
                f'Component {component_name} has no valid release. '
                f'Given reference version: {reference_version}'
            )
        return latest_version

    def greatest_release_before(self, component_name: str, version: str):
        component_reference = ComponentReference.create(name=component_name, version=version)
        repo_helper = self._repository_helper(component_reference)
        version = ver.parse_to_semver(version)

        # greatest version comes last
        versions = sorted(repo_helper.release_versions(), key=ver.parse_to_semver)
        versions = [v for v in versions if ver.parse_to_semver(v) < version]

        if len(versions) == 0:
            return None # no release before current was found
        return versions[-1]


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


@dataclasses.dataclass
class ComponentDiff:
    crefs_only_left: set = dataclasses.field(default_factory=set)
    crefs_only_right: set = dataclasses.field(default_factory=set)
    crefpairs_version_changed: set = dataclasses.field(default_factory=set)
    names_only_left: set = dataclasses.field(default_factory=set)
    names_only_right: set = dataclasses.field(default_factory=set)
    names_version_changed: set = dataclasses.field(default_factory=set)


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


@dataclasses.dataclass
class ImageDiff:
    left_component: Component
    right_component: Component
    irefs_only_left: set = dataclasses.field(default_factory=set)
    irefs_only_right: set = dataclasses.field(default_factory=set)
    irefpairs_version_changed: set = dataclasses.field(default_factory=set)
    names_only_left: set = dataclasses.field(default_factory=set)
    names_only_right: set = dataclasses.field(default_factory=set)
    names_version_changed: set = dataclasses.field(default_factory=set)


def diff_images(
    left_component_descriptor,
    right_component_descriptor,
    left_component,
    right_component
):
    left_images = set(_effective_images(left_component_descriptor, left_component))
    right_images = set(_effective_images(right_component_descriptor, right_component))

    left_names_to_imgs = {i.name(): i for i in left_images}
    right_names_to_imgs = {i.name(): i for i in right_images}

    img_diff = ImageDiff(
        left_component=left_component,
        right_component=right_component,
    )

    if left_images == right_images:
        return img_diff

    for name, img in left_names_to_imgs.items():
        if not name in right_names_to_imgs:
            img_diff.irefs_only_left.add(img)

    for name, img in right_names_to_imgs.items():
        if not name in left_names_to_imgs:
            img_diff.irefs_only_right.add(img)

    lgroups = list(_grouped_effective_images(
        left_component,
        component_descriptor=left_component_descriptor,
        )
    )
    rgroups = list(_grouped_effective_images(
        right_component,
        component_descriptor=right_component_descriptor,
        )
    )

    def enumerate_group_pairs(lgroups, rgroups):
        for lgroup in lgroups:
            lgroup = list(lgroup)
            # img-group must always be non-empty
            img_name = lgroup[0].name()
            if not img_name in right_names_to_imgs:
                continue # not all images exist on both sides
            for rgroup in rgroups:
                rgroup = list(rgroup)
                if not rgroup[0].name() == img_name:
                    continue
                else:
                    yield (lgroup, rgroup)

    for lgroup, rgroup in enumerate_group_pairs(lgroups, rgroups):
        # trivial case: image groups have length of 1
        if len(lgroup) == 1 and len(rgroup) == 1:
            if lgroup[0].version() != rgroup[0].version():
                img_diff.irefpairs_version_changed.add((lgroup[0], rgroup[0]))
            continue

        lgroup = sorted(lgroup)
        rgroup = sorted(rgroup)

        # remove all images present in both
        versions_in_both = {
            i.version() for i in lgroup
        } & {
            i.version() for i in rgroup
        }
        lgroup = [
            i for i in lgroup
            if not i.version() in versions_in_both
        ]
        rgroup = [
            i for i in rgroup
            if not i.version() in versions_in_both
        ]

        i = 0
        for i, left_image in enumerate(lgroup):
            if i >= len(rgroup):
                img_diff.irefs_only_left.add(left_image)
            else:
                right_image = rgroup[i]
                img_diff.irefpairs_version_changed.add((left_image, right_image))

        lgroup = lgroup[i:]
        rgroup = rgroup[i:]

        for i in lgroup:
            img_diff.irefs_only_left.add(i)

        for i in rgroup:
            img_diff.irefs_only_right.add(i)

    return img_diff


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
            key=lambda r: ver.parse_to_semver(r.version()),
        )
        # greates version comes last
        yield matching_refs[-1]


def _enumerate_images(
    component_descriptor: ComponentDescriptor,
    image_reference_filter=lambda _: True,
) -> typing.Iterable[typing.Tuple[Component, ContainerImage]]:
    for component in component_descriptor.components():
        component_dependencies = component.dependencies()
        for container_image in filter(
                image_reference_filter,
                component_dependencies.container_images()
        ):
            yield (component, container_image)


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


def guess_commit_from_ref(component: Component):
    """
    heuristically guess the appropriate git-ref for the given component's version
    """
    github_api = ccc.github.github_api_from_component(component=component)
    github_repo = github_api.repository(
        component.github_organisation(),
        component.github_repo(),
    )

    def in_repo(commit_ish):
        try:
            return github_repo.ref(commit_ish).object.sha
        except github3.exceptions.NotFoundError:
            pass

        try:
            return github_repo.commit(commit_ish).sha
        except (github3.exceptions.UnprocessableEntity, github3.exceptions.NotFoundError):
            return None

    # first guess: component version could already be a valid "Gardener-relaxed-semver"
    version_str = str(version.parse_to_semver(component))
    commit = in_repo(version_str)
    if commit:
        return commit
    # also try unmodified version-str
    if commit := in_repo(component.version()):
        return commit

    # second guess: split commit-hash after last `-` character (inject-commit-hash semantics)
    if '-' in (version_str := str(component.version())):
        last_part = version_str.split('-')[-1]
        commit = in_repo(last_part)
        if commit:
            return commit

    # third guess: branch
    try:
        return github_repo.branch(version_str).commit.sha
    except github3.exceptions.NotFoundError:
        pass

    # still unknown commit-ish throw error
    raise RefGuessingFailedError(
        f'failed to guess on ref for {component.name()=}{component.version()=}'
    )
