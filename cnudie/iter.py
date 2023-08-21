import dataclasses
import typing

import gci.componentmodel as cm
import cnudie.retrieve


@dataclasses.dataclass
class Node:
    path: tuple[cm.Component]

    @property
    def component(self):
        return self.path[-1]

    @property
    def component_id(self):
        return self.component.identity()


@dataclasses.dataclass
class ComponentNode(Node):
    comp_refs: tuple[tuple[cm.Component]] = dataclasses.field(default_factory=lambda: tuple(tuple()))


@dataclasses.dataclass
class ResourceNode(Node):
    resource: cm.Resource


@dataclasses.dataclass
class SourceNode(Node):
    source: cm.ComponentSource


class Filter:
    @staticmethod
    def components(node: Node):
        return isinstance(node, ComponentNode)

    @staticmethod
    def resources(node: Node):
        return isinstance(node, ResourceNode)

    @staticmethod
    def sources(node: Node):
        return isinstance(node, SourceNode)


def iter(
    component: cm.Component,
    lookup: cnudie.retrieve.ComponentDescriptorLookupById=None,
    recursion_depth: int=-1,
    prune_unique: bool=True,
    node_filter: typing.Callable[[Node], bool]=None,
    ctx_repo: cm.RepositoryContext | str=None,
    include_comp_refs: bool=False,
):
    '''
    returns a generator yielding the transitive closure of nodes accessible from the given component.

    See `cnudie.retrieve` for retrieving components/component descriptors.

    @param component:    root component for iteration
    @param lookup:       used to lookup referenced components descriptors
                         (thus abstracting from retrieval method)
                         optional iff recursion_depth is set to 0
    @param recursion_depth: if set to a positive value, limit recursion for resolving component
                            dependencies; -1 will resolve w/o recursion limit, 0 will not resolve
                            component dependencies
    @param prune_unique: if true, redundant component-versions will only be traversed once
    @node_filter:        use to filter emitted nodes (see Filter for predefined filters)
    @param ctx_repo:     optional ctx_repo to be used to override in the lookup
    @param include_comp_refs: if true, each component node will also have a attribute which
                              specifies in which components it is referenced. This will slow
                              down execution time as this can only be calculated if all
                              nodes have been traversed
    '''
    if isinstance(component, cm.ComponentDescriptor):
        component = component.component

    seen_component_ids = set()

    if not lookup and not recursion_depth == 0:
        raise ValueError('lookup is required if recusion is not disabled (recursion_depth==0)')

    if include_comp_refs:
        nodes = list()

    # need to nest actual iterator to keep global state of seen component-IDs
    def inner_iter(
        component: cm.Component,
        lookup: cnudie.retrieve.ComponentDescriptorLookupById,
        recursion_depth,
        path: tuple[cm.ComponentIdentity]=(),
    ):
        path = (*path, component)

        yield ComponentNode(
            path=path,
        )

        for resource in component.resources:
            yield ResourceNode(
                path=path,
                resource=resource,
            )

        for source in component.sources:
            yield SourceNode(
                path=path,
                source=source,
            )

        if recursion_depth == 0:
            return # stop resolving referenced components
        elif recursion_depth > 0:
            recursion_depth -= 1

        for cref in component.componentReferences:
            cref_id = cm.ComponentIdentity(
                name=cref.componentName,
                version=cref.version,
            )
            if ctx_repo:
                referenced_component_descriptor = lookup(cref_id, ctx_repo)
            else:
                referenced_component_descriptor = lookup(cref_id)
            referenced_component = referenced_component_descriptor.component

            yield from inner_iter(
                component=referenced_component,
                lookup=lookup,
                recursion_depth=recursion_depth,
                path=path,
            )

    for node in inner_iter(
        component=component,
        lookup=lookup,
        recursion_depth=recursion_depth,
        path=(),
    ):
        if node_filter and not node_filter(node):
            continue

        if include_comp_refs and isinstance(node, ComponentNode):
            # check if same component was already appended with another component ref
            comp_refs = next((n.comp_refs for n in nodes if node.component.name == n.component.name and node.component.version == n.component.version and isinstance(n, ComponentNode)), tuple())
            if node.path not in comp_refs:
                comp_refs = (*comp_refs, node.path)
            node.comp_refs = comp_refs

            # update component refs property for already appended component nodes as well
            nodes = [
                n if node.component.name != n.component.name or node.component.version != n.component.version or not isinstance(n, ComponentNode)
                else ComponentNode(
                    path=n.path,
                    comp_refs=comp_refs,
                )
                for n in nodes
            ]

        if prune_unique and isinstance(node, ComponentNode):
            if node.component.identity() in seen_component_ids:
                continue
            else:
                seen_component_ids.add(node.component_id)

        if include_comp_refs:
            nodes.append(node)
        else:
            yield node

    if include_comp_refs:
        yield from nodes
