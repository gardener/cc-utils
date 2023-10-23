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
    pass


class ArtefactNode:
    '''
    mixin class intended to be used for ResourceNode and SourceNode to add some convenience:

    - artefact property (useful for iterating over mixed node-types)
    - iterable (useful for pattern-matching, e.g. c,a = node)
    '''
    @property
    def artefact(self) -> cm.Resource | cm.ComponentSource:
        if isinstance(self, ResourceNode):
            return self.resource
        if isinstance(self, SourceNode):
            return self.source
        raise TypeError('must be of type ResourceNode or SourceNode')

    def __iter__(self) -> typing.Generator[cm.Component|cm.Resource|cm.ComponentSource, None, None]:
        # pylint: disable=E1101
        yield self.component
        yield self.artefact


@dataclasses.dataclass
class ResourceNode(Node, ArtefactNode):
    resource: cm.Resource


@dataclasses.dataclass
class SourceNode(Node, ArtefactNode):
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
    ctx_repo: cm.OcmRepository | str=None,
) -> typing.Generator[Node, None, None]:
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
    @param ctx_repo:     optional OCM Repository to be used to override in the lookup
    '''
    if isinstance(component, cm.ComponentDescriptor):
        component = component.component

    seen_component_ids = set()

    if not lookup and not recursion_depth == 0:
        raise ValueError('lookup is required if recusion is not disabled (recursion_depth==0)')

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

        if prune_unique and isinstance(node, ComponentNode):
            if node.component.identity() in seen_component_ids:
                continue
            else:
                seen_component_ids.add(node.component_id)

        yield node


def iter_resources(
    component: cm.Component,
    lookup: cnudie.retrieve.ComponentDescriptorLookupById=None,
    recursion_depth: int=-1,
    prune_unique: bool=True,
) -> typing.Generator[ResourceNode, None, None]:
    '''
    curried version of `iter` w/ node-filter preset to yield only resource-nodes
    '''
    return iter(
        component=component,
        lookup=lookup,
        recursion_depth=recursion_depth,
        prune_unique=prune_unique,
        node_filter=Filter.resources,
    )
