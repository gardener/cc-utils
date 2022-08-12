import dataclasses
import typing

import gci.componentmodel as cm


ComponentLookupById = typing.Callable[[cm.ComponentIdentity], cm.Component]


def dictbased_lookup(components: typing.Iterable[ComponentLookupById]):
    lookup_dict = {c.identity(): c for c in components}

    def lookup(component_id: cm.ComponentIdentity):
        return lookup_dict[component_id]

    return lookup


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
    pass


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
    lookup: ComponentLookupById,
    prune_unique: bool=True,
    node_filter: typing.Callable[[Node], bool]=None
):
    '''
    returns a generator yielding the transitive closure of nodes accessible from the given component.

    See `cnudie.retrieve` for retrieving components/component descriptors.

    @param component:    root component for iteration
    @param lookup:       used to lookup referenced components
                         (thus abstracting from retrieval method)
    @param prune_unique: if true, redundant component-versions will only be traversed once
    @node_filter:        use to filter emitted nodes (see Filter for predefined filters)
    '''
    seen_component_ids = set()

    # need to nest actual iterator to keep global state of seen component-IDs
    def inner_iter(
        component: cm.Component,
        lookup: ComponentLookupById,
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

        for cref in component.componentReferences:
            cref_id = cm.ComponentIdentity(
                name=cref.componentName,
                version=cref.version,
            )
            referenced_component = lookup(cref_id)

            yield from inner_iter(
                component=referenced_component,
                lookup=lookup,
                path=path,
            )

    for node in inner_iter(
        component=component,
        lookup=lookup,
        path=(),
    ):
        if node_filter and not node_filter(node):
            continue

        if prune_unique and isinstance(node, ComponentNode):
            if node.component.identity() in seen_component_ids:
                continue
            else:
                seen_component_ids.add(node.component_id)

        yield node
