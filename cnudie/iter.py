import collections.abc
import dataclasses
import enum

import cnudie.retrieve
import dso.labels
import gci.componentmodel as cm


class NodeReferenceType(enum.StrEnum):
    COMPONENT_REFERENCE = 'componentReference'
    EXTRA_COMPONENT_REFS_LABEL = f'label:{dso.labels.ExtraComponentReferencesLabel.name}'


@dataclasses.dataclass(frozen=True)
class NodePathEntry:
    component: cm.Component
    reftype: NodeReferenceType = NodeReferenceType.COMPONENT_REFERENCE


@dataclasses.dataclass
class Node:
    path: tuple[NodePathEntry]

    @property
    def component(self):
        return self.path[-1].component

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
    def artefact(self) -> cm.Resource | cm.Source:
        if isinstance(self, ResourceNode):
            return self.resource
        if isinstance(self, SourceNode):
            return self.source
        raise TypeError('must be of type ResourceNode or SourceNode')

    def __iter__(
        self,
    ) -> collections.abc.Generator[cm.Component | cm.Resource | cm.Source, None, None]:
        # pylint: disable=E1101
        yield self.component
        yield self.artefact


@dataclasses.dataclass
class ResourceNode(Node, ArtefactNode):
    resource: cm.Resource


@dataclasses.dataclass
class SourceNode(Node, ArtefactNode):
    source: cm.Source


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
    node_filter: collections.abc.Callable[[Node], bool]=None,
    ocm_repo: cm.OcmRepository | str=None,
    ctx_repo: cm.OcmRepository | str=None, # deprecated, use `ocm_repo` instead
    component_filter: collections.abc.Callable[[cm.Component], bool]=None,
    reftype_filter: collections.abc.Callable[[NodeReferenceType], bool]=None,
) -> collections.abc.Generator[Node, None, None]:
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
    @param node_filter:  use to filter emitted nodes (see Filter for predefined filters)
    @param ocm_repo:     optional OCM Repository to be used to override in the lookup
    @param ctx_repo:     deprecated, use `ocm_repo` instead
    @param component_filter: use to exclude components (and their references) from the iterator;
                             thereby `True` means the component should be filtered out
    @param reftype_filter: use to exclude components (and their references) from the iterator if
                           they are of a certain reference type; thereby `True` means the component
                           should be filtered out
    '''
    if not ocm_repo and ctx_repo:
        ocm_repo = ctx_repo

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
        path: tuple[NodePathEntry]=(),
        reftype: NodeReferenceType=NodeReferenceType.COMPONENT_REFERENCE,
    ):
        if component_filter and component_filter(component):
            return

        if reftype_filter and reftype_filter(reftype):
            return

        path = (*path, NodePathEntry(component, reftype))

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

            if ocm_repo:
                referenced_component_descriptor = lookup(cref_id, ocm_repo)
            else:
                referenced_component_descriptor = lookup(cref_id)

            yield from inner_iter(
                component=referenced_component_descriptor.component,
                lookup=lookup,
                recursion_depth=recursion_depth,
                path=path,
            )

        if not (extra_crefs_label := component.find_label(
            name=dso.labels.ExtraComponentReferencesLabel.name,
        )):
            return

        extra_crefs_label: dso.labels.ExtraComponentReferencesLabel = dso.labels.deserialise_label(
            label=extra_crefs_label,
        )

        for extra_cref in extra_crefs_label.value:
            extra_cref_id = extra_cref.component_reference

            if ocm_repo:
                referenced_component_descriptor = lookup(extra_cref_id, ocm_repo)
            else:
                referenced_component_descriptor = lookup(extra_cref_id)

            yield from inner_iter(
                component=referenced_component_descriptor.component,
                lookup=lookup,
                recursion_depth=recursion_depth,
                path=path,
                reftype=NodeReferenceType.EXTRA_COMPONENT_REFS_LABEL,
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
    component_filter: collections.abc.Callable[[cm.Component], bool]=None,
    reftype_filter: collections.abc.Callable[[NodeReferenceType], bool]=None,
) -> collections.abc.Generator[ResourceNode, None, None]:
    '''
    curried version of `iter` w/ node-filter preset to yield only resource-nodes
    '''
    return iter(
        component=component,
        lookup=lookup,
        recursion_depth=recursion_depth,
        prune_unique=prune_unique,
        node_filter=Filter.resources,
        component_filter=component_filter,
        reftype_filter=reftype_filter,
    )
