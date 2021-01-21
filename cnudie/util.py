import typing
import dataclasses
import functools

import deprecated

from ci.util import FluentIterable
import product.v2

import gci.componentmodel as cm


def to_component(*args, **kwargs) -> cm.Component:
    if not kwargs and len(args) == 1:
        component = args[0]
        if isinstance(component, cm.Component):
            return component
        elif isinstance(component, cm.ComponentDescriptor):
            return component.component
        else:
            raise ValueError(args)
    raise NotImplementedError


def determine_main_source_for_component(
    component: cm.Component,
    absent_ok: bool=True,
) -> cm.ComponentSource:
    for source in component.sources:
        if label := source.find_label('cloud.gardener/cicd/source'):
            if label.value.get('repository-classification') == 'main':
                return source

    if not component.sources:
        if absent_ok:
            return None
        raise ValueError(f'no sources defined by {component=}')

    # if no label was found use heuristic approach
    # heuristic: use first source
    return component.sources[0]


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


@deprecated.deprecated
def diff_component_descriptors(
    left_component: typing.Union[cm.ComponentDescriptor, cm.Component],
    right_component: typing.Union[cm.ComponentDescriptor, cm.Component],
    ignore_component_names=(),
    cache_dir=None,
    components_resolv_func: typing.Callable[
        [cm.ComponentDescriptor, str], typing.Sequence[cm.Component]
    ]=product.v2.components,
) -> ComponentDiff:
    if isinstance(left_component, cm.ComponentDescriptor):
        left_component = left_component.component
    if isinstance(right_component, cm.ComponentDescriptor):
        right_component = right_component.component

    if not isinstance(left_component, cm.Component):
        raise TypeError(
            f'left product unsupported type {type(left_component)=}.'
        )
    if not isinstance(right_component, cm.Component):
        raise TypeError(
            f'unsupported type {type(right_component)=}.'
        )
    # only take component references into account for now and assume
    # that component versions are always identical content-wise
    left_components: typing.Generator[cm.Component] = components_resolv_func(
        component_descriptor_v2=left_component,
        cache_dir=cache_dir,
    )
    right_components: typing.Generator[cm.Component] = components_resolv_func(
        component_descriptor_v2=right_component,
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
    left_component_identities = {
        c.identity() for c in left_components if c.name not in ignore_component_names
    }
    right_component_identities = {
        c.identity() for c in right_components if c.name not in ignore_component_names
    }

    left_only_component_identities = left_component_identities - right_component_identities
    right_only_component_identities = right_component_identities - left_component_identities

    if left_only_component_identities == right_only_component_identities:
        return None # no diff

    left_components = tuple((
        c for c in left_components if c.identity() in left_only_component_identities
    ))
    right_components = tuple((
        c for c in right_components if c.identity() in right_only_component_identities
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

    left_component_names = {i.name for i in left_component_identities}
    right_component_names = {i.name for i in right_component_identities}
    names_version_changed = {c[0].name for c in components_with_changed_versions}

    both_names = left_component_names & right_component_names
    left_component_names -= both_names
    right_component_names -= both_names

    return ComponentDiff(
        cidentities_only_left=left_only_component_identities,
        cidentities_only_right=right_only_component_identities,
        cpairs_version_changed=components_with_changed_versions,
        names_only_left=left_component_names,
        names_only_right=right_component_names,
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
    if (res.name, res.version) not in [(res.name, res.version) for res in list]:
        list.append(res)


def diff_resources(
    left_component: cm.Component,
    right_component: cm.Component,
) -> ResourceDiff:
    if type(left_component) is not cm.Component:
        raise NotImplementedError(
            f'unsupported {type(left_component)=}',
        )
    if type(right_component) is not cm.Component:
        raise NotImplementedError(
            f'unsupported {type(right_component)=}',
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
    # get left exclusive resources
    for resource in left_resource_identities.values():
        if not resource.name in right_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_left, resource)

    # get right exclusive resources
    for resource in right_resource_identities_to_resource.values():
        if not resource.name in left_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_right, resource)

    # groups the resources by name. The version will be used at a later point
    def enumerate_group_pairs(
        left_resources: typing.List[cm.Resource],
        right_resources: typing.List[cm.Resource]
    ) -> typing.Tuple[typing.List[cm.Resource], typing.List[cm.Resource]]:
        # group the resources with the same name on both sides
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

        # remove all resources present in both
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
