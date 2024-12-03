import collections.abc
import dataclasses
import graphlib
import textwrap

import deprecated

import ci.util
import ocm
import oci.model as om

ComponentId = (
    ocm.Component
    | ocm.ComponentDescriptor
    | ocm.ComponentIdentity
    | ocm.ComponentReference
    | str
    | tuple[str, str]
)
ComponentName = (
    ocm.Component
    | ocm.ComponentReference
    | ocm.ComponentDescriptor
    | ocm.ComponentIdentity
    | str
)

META_SEPARATOR = '.build-'


def to_component_id(
    component: ComponentId, /
) -> ocm.ComponentIdentity:
    if isinstance(component, ocm.ComponentIdentity):
        return component

    if isinstance(component, ocm.ComponentDescriptor) or hasattr(component, 'component'):
        component = component.component
        # fall through to next case
    if isinstance(component, ocm.Component) or hasattr(component, 'name') \
        and not hasattr(component, 'componentName'):
        name = component.name
        version = component.version
    if isinstance(component, ocm.ComponentReference) or hasattr(component, 'componentName'):
        name = component.componentName
        version = component.version
    if isinstance(component, str):
        name, version = component.split(':', 1)
    if isinstance(component, tuple):
        name, version = component

    return ocm.ComponentIdentity(
        name=name,
        version=version,
    )


def to_component_name(
    component: ComponentName,
) -> str:
    if isinstance(component, ocm.ComponentDescriptor):
        component = component.component
    if isinstance(component, ocm.Component):
        component = component.name
    elif isinstance(component, ocm.ComponentIdentity):
        component = component.name
    elif isinstance(component, ocm.ComponentReference):
        component = component.componentName
    elif isinstance(component, tuple):
        if not len(component) == 2:
            raise ValueError('expected two-tuple with two elements')
        component = component[0]
    if not isinstance(component, str):
        raise ValueError(component)

    if ':' in component:
        # assumption: has form <name>:<version>
        # let exception raise in other cases
        component, _ = component.split(':')

    return component


def to_component_id_and_repository_url(
    component: ocm.Component | ocm.ComponentDescriptor | ocm.ComponentIdentity | str,
    repository: ocm.OciOcmRepository|str=None,
):
    if isinstance(component, str):
        name, version = component.rsplit(':', 1)
        component = ocm.ComponentIdentity(
            name=name,
            version=version,
        )

    if isinstance(component, ocm.ComponentDescriptor):
        component = component.component
    elif isinstance(component, ocm.ComponentIdentity) and not repository:
        raise ValueError('repository must be passed if calling w/ component-identity')

    if not repository: # component is sure to be of type ocm.Component by now (checked above)
        component: ocm.Component
        repository = component.current_ocm_repo

    if isinstance(repository, ocm.OciOcmRepository):
        repo_base_url = repository.baseUrl
    elif isinstance(repository, str):
        repo_base_url = repository
    else:
        raise ValueError(f'only OciOcmRepository is supported - got: {repository=}')

    return component, repo_base_url


def oci_ref(
    component: ocm.Component | ocm.ComponentDescriptor | ocm.ComponentIdentity | str,
    repository: ocm.OciOcmRepository|str=None,
) -> om.OciImageReference:
    component, repo_base_url = to_component_id_and_repository_url(
        component=component,
        repository=repository,
    )

    return om.OciImageReference(
        ci.util.urljoin(
            repo_base_url,
            'component-descriptors',
            f'{component.name.lower()}:{component.version}',
        )
    )


def iter_sorted(
    components: collections.abc.Iterable[ocm.Component], /
) -> collections.abc.Generator[ocm.Component, None, None]:
    '''
    returns a generator yielding the given components, honouring their dependencies, starting
    with "leaf" components (i.e. components w/o dependencies), also known as topologically sorted.
    '''
    components = (to_component(c) for c in components)
    components_by_id = {c.identity(): c for c in components}

    toposorter = graphlib.TopologicalSorter()

    def ref_to_comp_id(component_ref: ocm.ComponentReference) -> ocm.ComponentIdentity:
        return ocm.ComponentIdentity(
            name=component_ref.componentName,
            version=component_ref.version,
        )

    for component_id, component in components_by_id.items():
        depended_on_comp_ids = (
            ref_to_comp_id(cref)
            for cref in component.componentReferences
        )
        toposorter.add(component_id, *depended_on_comp_ids)

    for component_id in toposorter.static_order():
        if not component_id in components_by_id:
            # XXX: ignore component-references not contained in passed components for now
            continue

        yield components_by_id[component_id]


def to_component(*args, **kwargs) -> ocm.Component:
    if not kwargs and len(args) == 1:
        component = args[0]
        if isinstance(component, ocm.Component):
            return component
        elif isinstance(component, ocm.ComponentDescriptor):
            return component.component
        else:
            raise ValueError(args)
    raise NotImplementedError


def determine_component_name(
    repository_hostname: str,
    repository_path: str,
) -> str:
    component_name = '/'.join((
        repository_hostname,
        repository_path,
    ))
    return component_name.lower() # OCI demands lowercase


def normalise_component_name(component_name: str) -> str:
    return component_name.lower() # oci-spec demands lowercase


def oci_artefact_reference(
        component: (
            ocm.Component
            | ocm.ComponentIdentity
            | ocm.ComponentReference
            | str # 'name:version'
            | tuple[str, str] # (name, version)
        ),
        ocm_repository: str | ocm.OciOcmRepository = None
) -> str:
    if isinstance(component, ocm.Component):
        if not ocm_repository:
            ocm_repository = component.current_ocm_repo
        component_name = component.name
        component_version = component.version

    elif isinstance(component, ocm.ComponentIdentity):
        component_name = component.name
        component_version = component.version

    elif isinstance(component, ocm.ComponentReference):
        component_name = component.componentName
        component_version = component.version

    elif isinstance(component, str):
        component_name, component_version = component.split(':')

    elif isinstance(component, tuple):
        if not len(component) == 2 or not (
            isinstance(component[0], str) and isinstance(component[1], str)
        ):
            raise TypeError("If a tuple is given as component, it must contain two strings.")
        component_name, component_version = component
    else:
        raise ValueError(component)

    if not ocm_repository:
        raise ValueError('ocm_repository must be given unless a Component is passed.')
    elif isinstance(ocm_repository, str):
        ocm_repository = ocm.OciOcmRepository(baseUrl=ocm_repository)
    elif isinstance(ocm_repository, ocm.OciOcmRepository):
        ocm_repository = ocm_repository
    else:
        raise TypeError(type(ocm_repository))

    return ocm_repository.component_version_oci_ref(
        name=component_name,
        version=component_version,
    )


def target_oci_ref(
    component: ocm.Component,
    component_ref: ocm.ComponentReference=None,
    component_version: str=None,
):
    if not component_ref:
        component_ref = component
        component_name = component_ref.name
    else:
        component_name = component_ref.componentName

    component_name = normalise_component_name(component_name)
    component_version = component_ref.version

    last_ocm_repo = component.current_ocm_repo

    return last_ocm_repo.component_version_oci_ref(
        name=component_name,
        version=component_version,
    )


@deprecated.deprecated('use ocm.util.main_source instead')
def main_source(
    component: ocm.Component,
    absent_ok: bool=True,
) -> ocm.Source:
    component = to_component(component)
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


determine_main_source_for_component = main_source


@dataclasses.dataclass(frozen=True)
class ComponentResource:
    component: ocm.Component
    resource: ocm.Resource


@dataclasses.dataclass(frozen=True)
class LabelDiff:
    labels_only_left: list[ocm.Label] = dataclasses.field(default_factory=list)
    labels_only_right: list[ocm.Label] = dataclasses.field(default_factory=list)
    label_pairs_changed: list[tuple[ocm.Label, ocm.Label]] = dataclasses.field(default_factory=list)


empty_list = lambda: dataclasses.field(default_factory=list) # noqa:E3701


@dataclasses.dataclass
class ComponentDiff:
    cidentities_only_left: set = empty_list()
    cidentities_only_right: set = empty_list()
    cpairs_version_changed: list[tuple[ocm.Component, ocm.Component]] = empty_list
    # only set when new component is added/removed
    names_only_left: set = dataclasses.field(default_factory=set)
    names_only_right: set = dataclasses.field(default_factory=set)
    # only set on update
    names_version_changed: set = dataclasses.field(default_factory=set)


def diff_labels(
    left_labels: list[ocm.Label],
    right_labels: list[ocm.Label],
) -> LabelDiff:

    left_label_name_to_label = {l.name: l for l in left_labels}
    right_label_name_to_label = {l.name: l for l in right_labels}

    labels_only_left = [
        l for l in left_labels if l.name not in right_label_name_to_label.keys()
    ]
    labels_only_right = [
        l for l in right_labels if l.name not in left_label_name_to_label.keys()
    ]
    label_pairs_changed = []
    for left_label, right_label in _enumerate_group_pairs(
        left_elements=left_labels,
        right_elements=right_labels,
        unique_name=True,
    ):
        if left_label.value == right_label.value:
            continue
        else:
            label_pairs_changed.append((left_label, right_label))

    return LabelDiff(
        labels_only_left=labels_only_left,
        labels_only_right=labels_only_right,
        label_pairs_changed=label_pairs_changed,
    )


def diff_components(
    left_components: collections.abc.Iterable[ocm.Component],
    right_components: collections.abc.Iterable[ocm.Component],
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
        changed_component: ocm.Component,
        components: list[ocm.Component],
    ):
        for c in components:
            if c.name == changed_component.name:
                return (changed_component, c)
        return (changed_component, None) # no pair component found

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


def format_component_diff(
    component_diff: ComponentDiff,
    delivery_dashboard_url_view_diff: str | None=None,
    delivery_dashboard_url: str | None=None
):
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

        # Group resources by name
        old_resources_grouped = {}
        new_resources_grouped = {}

        for res in old_component.resources:
            old_resources_grouped.setdefault(res.name, []).append(res)

        for res in new_component.resources:
            new_resources_grouped.setdefault(res.name, []).append(res)

        # Process each resource in the new component
        for res_name, new_res_list in new_resources_grouped.items():
            old_res_list = old_resources_grouped.get(res_name, [])

            if (
                old_res_list
                and sorted([res.version for res in old_res_list])
                == sorted([res.version for res in new_res_list])
            ):
                # Skip resource as all versions are identical in both the old and new components
                continue

            if len(new_res_list) == 1 and len(old_res_list) == 1:
                # Single occurrence in both -> Compare versions
                new_res = new_res_list[0]
                old_res = old_res_list[0]
                if new_res.version != old_res.version:
                    changed_resources.append(
                        [f'\U0001F504 {res_name}', f'{old_res.version} → {new_res.version}']
                    )
            else:
                # Multiple occurrences -> Display all versions for each resource name
                if old_res_list:
                    removed_resources.extend(
                        [[f'\U00002796 {res_name}', res.version] for res in old_res_list]
                    )
                if new_res_list:
                    added_resources.extend(
                        [[f'\U00002795 {res_name}', res.version] for res in new_res_list]
                    )

        # Process resources that only exist in the old component
        for res_name, old_res_list in old_resources_grouped.items():
            if res_name not in new_resources_grouped:
                removed_resources.extend(
                    [[f'\U00002796 {res_name}', res.version] for res in old_res_list]
                )

        # Aggregate results for resources into `resources_data`
        if not (added_resources or removed_resources or changed_resources):
            resources_data = [['No resources added, removed, or changed', '']]
        else:
            resources_data = added_resources + removed_resources + changed_resources

        #Import `tabulate` only when the function is called to avoid loading it during module import
        import tabulate
        # Generate HTML table with tabulate
        resources_table = tabulate.tabulate(
            tabular_data=resources_data,
            headers=['Resource', 'Version Change'],
            tablefmt='html'
        )

        component_details.append(component_header + resources_table + '\n</details>')

    return (
        bom_diff_header
        + summary_counts
        + '\n'.join(summary_details)
        + '\n## Component Details:\n'
        + '\n'.join(component_details)
    )


def _enumerate_group_pairs(
    left_elements: collections.abc.Sequence[ocm.Resource | ocm.Source | ocm.Label],
    right_elements: collections.abc.Sequence[ocm.Resource | ocm.Source, ocm.Label],
    unique_name: bool = False,
) -> collections.abc.Generator[tuple[list, list], None, None] | \
collections.abc.Generator[tuple, None, None]:
    '''Groups elements of two sequences with the same name.

    Can be used for Resources, Sources and Label
    '''
    # group the resources with the same name on both sides
    for element in left_elements:
        right_elements_group = [e for e in right_elements if e.name == element.name]
        # get resources for one resource via name

        # key is always in left group so we only have to check the length of the right group
        if len(right_elements_group) == 0:
            continue
        else:
            left_elements_group = [e for e in left_elements if e.name == element.name]

            if unique_name:
                if len(left_elements_group) == 1 and len(right_elements_group) == 1:
                    yield (left_elements_group[0], right_elements_group[0])
                else:
                    raise RuntimeError(
                        f'Element name "{element.name}"" is not unique at least one list. '
                        f'{len(left_elements_group)=} {len(right_elements_group)=}')
            else:
                yield (left_elements_group, right_elements_group)


@dataclasses.dataclass
class ResourceDiff:
    left_component: ocm.Component
    right_component: ocm.Component
    resource_refs_only_left: list[ocm.Resource] = dataclasses.field(default_factory=list)
    resource_refs_only_right: list[ocm.Resource] = dataclasses.field(default_factory=list)
    resourcepairs_version_changed: list[tuple[ocm.Resource, ocm.Resource]] = dataclasses.field(default_factory=list) # noqa:E501


def _add_if_not_duplicate(list, res):
    if (res.name, res.version) not in [(res.name, res.version) for res in list]:
        list.append(res)


def diff_resources(
    left_component: ocm.Component,
    right_component: ocm.Component,
) -> ResourceDiff:
    if type(left_component) is not ocm.Component:
        raise NotImplementedError(
            f'unsupported {type(left_component)=}',
        )
    if type(right_component) is not ocm.Component:
        raise NotImplementedError(
            f'unsupported {type(right_component)=}',
        )

    left_resource_identities_to_resource = {
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

    if left_resource_identities_to_resource.keys() == right_resource_identities_to_resource.keys():
        return resource_diff

    left_names_to_resource = {r.name: r for r in left_component.resources}
    right_names_to_resource = {r.name: r for r in right_component.resources}
    # get left exclusive resources
    for resource in left_resource_identities_to_resource.values():
        if not resource.name in right_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_left, resource)

    # get right exclusive resources
    for resource in right_resource_identities_to_resource.values():
        if not resource.name in left_names_to_resource:
            _add_if_not_duplicate(resource_diff.resource_refs_only_right, resource)

    # groups the resources by name. The version will be used at a later point
    def enumerate_group_pairs(
        left_resources: list[ocm.Resource],
        right_resources: list[ocm.Resource]
    ) -> collections.abc.Generator[tuple[list[ocm.Resource], list[ocm.Resource]], None, None]:
        # group the resources with the same name on both sides
        for name in left_names_to_resource.keys():
            right_resource_group = [r for r in right_resources if r.name == name]

            # key is always in left group so we only have to check the length of the right group
            if len(right_resource_group) == 0:
                continue
            else:
                left_resource_group = [r for r in left_resources if r.name == name]
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

        # sort resources. Important because down/upgrades depend on position in list
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

        # at this point we got left and right resources with the same name but different versions
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


def sanitise_version(version: str) -> str:
    '''
    Additional build metadata as defined in SemVer can be added via `+` to the version. However,
    OCI registries don't support `+` as tag character, which is why it has to be sanitised, for
    example using `META_SEPARATOR`.
    '''
    sanitised_version = version.replace('+', META_SEPARATOR)

    return sanitised_version


def desanitise_version(version: str) -> str:
    '''
    This function reverts the sanitisation of the `sanitise_version` function, which allows
    processing the version the same way as prior to using `sanitise_version`.
    '''
    desanitised_version = version.replace(META_SEPARATOR, '+')

    return desanitised_version
