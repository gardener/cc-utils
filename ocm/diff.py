'''
component / resource / label diffing utilities (pure functions)
'''

import collections.abc
import dataclasses
import textwrap

import ocm


@dataclasses.dataclass(frozen=True)
class ComponentResource:
    component: ocm.Component
    resource: ocm.Resource


@dataclasses.dataclass(frozen=True)
class LabelDiff:
    labels_only_left: list[ocm.Label] = dataclasses.field(default_factory=list)
    labels_only_right: list[ocm.Label] = dataclasses.field(default_factory=list)
    label_pairs_changed: list[tuple[ocm.Label, ocm.Label]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ComponentDiff:
    cidentities_only_left: set = dataclasses.field(default_factory=set)
    cidentities_only_right: set = dataclasses.field(default_factory=set)
    cpairs_version_changed: list[tuple[ocm.Component, ocm.Component]] = dataclasses.field(
        default_factory=list,
    )
    # only set when new component is added/removed
    names_only_left: set = dataclasses.field(default_factory=set)
    names_only_right: set = dataclasses.field(default_factory=set)
    # only set on update
    names_version_changed: set = dataclasses.field(default_factory=set)


@dataclasses.dataclass
class ResourceDiff:
    left_component: ocm.Component
    right_component: ocm.Component
    resource_refs_only_left: list[ocm.Resource] = dataclasses.field(default_factory=list)
    resource_refs_only_right: list[ocm.Resource] = dataclasses.field(default_factory=list)
    resourcepairs_version_changed: list[tuple[ocm.Resource, ocm.Resource]] = dataclasses.field(
        default_factory=list,
    )


def _add_if_not_duplicate(lst: list, res: ocm.Resource) -> None:
    if (res.name, res.version) not in [(r.name, r.version) for r in lst]:
        lst.append(res)


def _enumerate_group_pairs(
    left_elements: collections.abc.Sequence[ocm.Resource | ocm.Source | ocm.Label],
    right_elements: collections.abc.Sequence[ocm.Resource | ocm.Source | ocm.Label],
    unique_name: bool=False,
) -> collections.abc.Generator:
    '''
    Groups elements of two sequences by name and yields (left_group, right_group) once per
    name present in both. With `unique_name=True`, asserts that each name appears at most once
    per side and yields (left_element, right_element) pairs.
    '''
    seen_names = set()
    for element in left_elements:
        if element.name in seen_names:
            continue
        seen_names.add(element.name)

        right_elements_group = [e for e in right_elements if e.name == element.name]
        if not right_elements_group:
            continue

        left_elements_group = [e for e in left_elements if e.name == element.name]

        if unique_name:
            if len(left_elements_group) == 1 and len(right_elements_group) == 1:
                yield (left_elements_group[0], right_elements_group[0])
            else:
                raise RuntimeError(
                    f'element name "{element.name}" is not unique in at least one list. '
                    f'{len(left_elements_group)=} {len(right_elements_group)=}',
                )
        else:
            yield (left_elements_group, right_elements_group)


def diff_labels(
    left_labels: list[ocm.Label],
    right_labels: list[ocm.Label],
) -> LabelDiff:
    left_label_names = {l.name for l in left_labels}
    right_label_names = {l.name for l in right_labels}

    labels_only_left = [l for l in left_labels if l.name not in right_label_names]
    labels_only_right = [l for l in right_labels if l.name not in left_label_names]

    label_pairs_changed = [
        (left_label, right_label)
        for left_label, right_label in _enumerate_group_pairs(
            left_elements=left_labels,
            right_elements=right_labels,
            unique_name=True,
        )
        if left_label.value != right_label.value
    ]

    return LabelDiff(
        labels_only_left=labels_only_left,
        labels_only_right=labels_only_right,
        label_pairs_changed=label_pairs_changed,
    )


def diff_components(
    left_components: collections.abc.Iterable[ocm.Component],
    right_components: collections.abc.Iterable[ocm.Component],
    ignore_component_names: collections.abc.Iterable[str]=(),
) -> ComponentDiff | None:
    left_component_identities = {
        c.identity() for c in left_components if c.name not in ignore_component_names
    }
    right_component_identities = {
        c.identity() for c in right_components if c.name not in ignore_component_names
    }

    left_only_component_identities = left_component_identities - right_component_identities
    right_only_component_identities = right_component_identities - left_component_identities

    if left_only_component_identities == right_only_component_identities:
        return None  # no diff

    left_components = tuple(
        c for c in left_components if c.identity() in left_only_component_identities
    )
    right_components = tuple(
        c for c in right_components if c.identity() in right_only_component_identities
    )

    def find_changed_component(
        changed_component: ocm.Component,
        components: collections.abc.Iterable[ocm.Component],
    ) -> tuple[ocm.Component, ocm.Component | None]:
        for c in components:
            if c.name == changed_component.name:
                return (changed_component, c)
        return (changed_component, None)

    components_with_changed_versions = []
    for component in left_components:
        changed_component = find_changed_component(component, right_components)
        if changed_component[1] is not None:
            components_with_changed_versions.append(changed_component)

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


def diff_resources(
    left_component: ocm.Component,
    right_component: ocm.Component,
) -> ResourceDiff:
    if not isinstance(left_component, ocm.Component):
        raise TypeError(f'{type(left_component)=}')
    if not isinstance(right_component, ocm.Component):
        raise TypeError(f'{type(right_component)=}')

    peers = left_component.resources + right_component.resources
    left_identities_to_resource = {r.identity(peers): r for r in left_component.resources}
    right_identities_to_resource = {r.identity(peers): r for r in right_component.resources}

    resource_diff = ResourceDiff(
        left_component=left_component,
        right_component=right_component,
    )

    if left_identities_to_resource.keys() == right_identities_to_resource.keys():
        return resource_diff

    left_names_to_resource = {r.name: r for r in left_component.resources}
    right_names_to_resource = {r.name: r for r in right_component.resources}

    # resources exclusive to either side
    for resource in left_identities_to_resource.values():
        if resource.name not in right_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_left, resource)
    for resource in right_identities_to_resource.values():
        if resource.name not in left_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_right, resource)

    for left_group, right_group in _enumerate_group_pairs(
        left_elements=left_component.resources,
        right_elements=right_component.resources,
    ):
        if len(left_group) == 1 and len(right_group) == 1:
            # if versions are equal resource will be ignored, resource is unchanged
            if left_group[0].version != right_group[0].version:
                resource_diff.resourcepairs_version_changed.append((left_group[0], right_group[0]))
            continue

        left_identities = {r.identity(peers): r for r in left_group}
        right_identities = {r.identity(peers): r for r in right_group}

        # sort resources. important because down/upgrades depend on position in list
        left_resources = [left_identities[i] for i in sorted(left_identities.keys())]
        right_resources = [right_identities[i] for i in sorted(right_identities.keys())]

        versions_in_both = {r.version for r in left_resources} & {r.version for r in right_resources}
        left_resources = [r for r in left_resources if r.version not in versions_in_both]
        right_resources = [r for r in right_resources if r.version not in versions_in_both]

        # at this point we have left and right resources with the same name but different versions
        for i, left_resource in enumerate(left_resources):
            if i >= len(right_resources):
                _add_if_not_duplicate(resource_diff.resource_refs_only_left, left_resource)
            else:
                right_resource = right_resources[i]
                resource_diff.resourcepairs_version_changed.append((left_resource, right_resource))

        # remaining resources on the longer side
        for r in left_resources[len(right_resources):]:
            _add_if_not_duplicate(resource_diff.resource_refs_only_left, r)
        for r in right_resources[len(left_resources):]:
            _add_if_not_duplicate(resource_diff.resource_refs_only_right, r)

    return resource_diff


def format_component_diff(
    component_diff: ComponentDiff,
    delivery_dashboard_url_view_diff: str | None=None,
    delivery_dashboard_url: str | None=None,
) -> str:
    if delivery_dashboard_url_view_diff:
        bom_diff_header = f'## <a href="{delivery_dashboard_url_view_diff}">BoM Diff</a>\n'
    else:
        bom_diff_header = '## BoM Diff\n'

    added_components = [
        f'\U00002795 {component.name} {component.version}'
        for component in component_diff.cidentities_only_right
        if component.name not in component_diff.names_version_changed
    ]

    removed_components = [
        f'\U00002796 {component.name} {component.version}'
        for component in component_diff.cidentities_only_left
        if component.name not in component_diff.names_version_changed
    ]

    changed_components = [
        f'\U00002699 {new_component.name}: {old_component.version} → {new_component.version}'
        for old_component, new_component in component_diff.cpairs_version_changed
    ]

    summary_counts = textwrap.dedent(f'''\
        Added components: {len(added_components)}
        Changed components: {len(changed_components)}
        Removed components: {len(removed_components)}\n
    ''')

    summary_details = []
    if added_components:
        summary_details.append('### Added Components:\n' + '\n'.join(added_components) + '\n')
    if removed_components:
        summary_details.append('### Removed Components:\n' + '\n'.join(removed_components) + '\n')
    if changed_components:
        summary_details.append('### Changed Components:\n' + '\n'.join(changed_components) + '\n')

    component_details = []
    for old_component, new_component in component_diff.cpairs_version_changed:
        if delivery_dashboard_url:
            component_link = (
                f"<a href='{delivery_dashboard_url}/#/component?name={new_component.name}'>"
                f'{new_component.name}</a>'
            )
        else:
            component_link = new_component.name

        component_header = (
            f'<details><summary>\U00002699 {component_link}:'
            f'{old_component.version} → {new_component.version}</summary>\n'
        )

        added_resources = []
        removed_resources = []
        changed_resources = []

        # group resources by name
        old_resources_grouped = {}
        new_resources_grouped = {}
        for res in old_component.resources:
            old_resources_grouped.setdefault(res.name, []).append(res)
        for res in new_component.resources:
            new_resources_grouped.setdefault(res.name, []).append(res)

        # process each resource in the new component
        for res_name, new_res_list in new_resources_grouped.items():
            old_res_list = old_resources_grouped.get(res_name, [])

            if (
                old_res_list
                and sorted(res.version for res in old_res_list)
                    == sorted(res.version for res in new_res_list)
            ):
                # skip resource as all versions are identical in both the old and new components
                continue

            if len(new_res_list) == 1 and len(old_res_list) == 1:
                new_res = new_res_list[0]
                old_res = old_res_list[0]
                if new_res.version != old_res.version:
                    changed_resources.append(
                        [f'\U0001F504 {res_name}', f'{old_res.version} → {new_res.version}'],
                    )
            else:
                # multiple occurrences -> display all versions for each resource name
                removed_resources.extend(
                    [f'\U00002796 {res_name}', res.version] for res in old_res_list
                )
                added_resources.extend(
                    [f'\U00002795 {res_name}', res.version] for res in new_res_list
                )

        # resources that only exist in the old component
        for res_name, old_res_list in old_resources_grouped.items():
            if res_name not in new_resources_grouped:
                removed_resources.extend(
                    [f'\U00002796 {res_name}', res.version] for res in old_res_list
                )

        if not (added_resources or removed_resources or changed_resources):
            resources_data = [['No resources added, removed, or changed', '']]
        else:
            resources_data = added_resources + removed_resources + changed_resources

        # import `tabulate` lazily to avoid loading it during module import
        import tabulate
        resources_table = tabulate.tabulate(
            tabular_data=resources_data,
            headers=['Resource', 'Version Change'],
            tablefmt='html',
        )

        component_details.append(component_header + resources_table + '\n</details>')

    return (
        bom_diff_header
        + summary_counts
        + '\n'.join(summary_details)
        + '\n## Component Details:\n'
        + '\n'.join(component_details)
    )
