import collections.abc

import cnudie.iter
import cnudie.retrieve_async
import dso.labels
import ocm


async def iter(
    component: ocm.Component,
    lookup: cnudie.retrieve_async.ComponentDescriptorLookupById=None,
    recursion_depth: int=-1,
    prune_unique: bool=True,
    node_filter: collections.abc.Callable[[cnudie.iter.Node], bool]=None,
    ocm_repo: ocm.OcmRepository | str=None,
    component_filter: collections.abc.Callable[[ocm.Component], bool]=None,
    reftype_filter: collections.abc.Callable[[cnudie.iter.NodeReferenceType], bool]=None,
) -> collections.abc.AsyncGenerator[cnudie.iter.Node, None, None]:
    '''
    returns a generator yielding the transitive closure of nodes accessible from the given component.

    See `cnudie.retrieve_async` for retrieving components/component descriptors.

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
    @param component_filter: use to exclude components (and their references) from the iterator;
                             thereby `True` means the component should be filtered out
    @param reftype_filter: use to exclude components (and their references) from the iterator if
                           they are of a certain reference type; thereby `True` means the component
                           should be filtered out
    '''
    if isinstance(component, ocm.ComponentDescriptor):
        component = component.component

    seen_component_ids = set()

    if not lookup and not recursion_depth == 0:
        raise ValueError('lookup is required if recusion is not disabled (recursion_depth==0)')

    # need to nest actual iterator to keep global state of seen component-IDs
    async def inner_iter(
        component: ocm.Component,
        lookup: cnudie.retrieve_async.ComponentDescriptorLookupById,
        recursion_depth,
        path: tuple[cnudie.iter.NodePathEntry]=(),
        reftype: cnudie.iter.NodeReferenceType=cnudie.iter.NodeReferenceType.COMPONENT_REFERENCE,
    ):
        if component_filter and component_filter(component):
            return

        if reftype_filter and reftype_filter(reftype):
            return

        path = (*path, cnudie.iter.NodePathEntry(component, reftype))

        yield cnudie.iter.ComponentNode(
            path=path,
        )

        for resource in component.resources:
            yield cnudie.iter.ResourceNode(
                path=path,
                resource=resource,
            )

        for source in component.sources:
            yield cnudie.iter.SourceNode(
                path=path,
                source=source,
            )

        if recursion_depth == 0:
            return # stop resolving referenced components
        elif recursion_depth > 0:
            recursion_depth -= 1

        for cref in component.componentReferences:
            cref_id = ocm.ComponentIdentity(
                name=cref.componentName,
                version=cref.version,
            )

            if ocm_repo:
                referenced_component_descriptor = await lookup(cref_id, ocm_repo)
            else:
                referenced_component_descriptor = await lookup(cref_id)

            async for node in inner_iter(
                component=referenced_component_descriptor.component,
                lookup=lookup,
                recursion_depth=recursion_depth,
                path=path,
            ):
                yield node

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
                referenced_component_descriptor = await lookup(extra_cref_id, ocm_repo)
            else:
                referenced_component_descriptor = await lookup(extra_cref_id)

            async for node in inner_iter(
                component=referenced_component_descriptor.component,
                lookup=lookup,
                recursion_depth=recursion_depth,
                path=path,
                reftype=cnudie.iter.NodeReferenceType.EXTRA_COMPONENT_REFS_LABEL,
            ):
                yield node

    async for node in inner_iter(
        component=component,
        lookup=lookup,
        recursion_depth=recursion_depth,
        path=(),
    ):
        if node_filter and not node_filter(node):
            continue

        if prune_unique and isinstance(node, cnudie.iter.ComponentNode):
            if node.component.identity() in seen_component_ids:
                continue
            else:
                seen_component_ids.add(node.component_id)

        yield node


def iter_resources(
    component: ocm.Component,
    lookup: cnudie.retrieve_async.ComponentDescriptorLookupById=None,
    recursion_depth: int=-1,
    prune_unique: bool=True,
    component_filter: collections.abc.Callable[[ocm.Component], bool]=None,
    reftype_filter: collections.abc.Callable[[cnudie.iter.NodeReferenceType], bool]=None,
) -> collections.abc.AsyncGenerator[cnudie.iter.ResourceNode, None, None]:
    '''
    curried version of `iter` w/ node-filter preset to yield only resource-nodes
    '''
    return iter(
        component=component,
        lookup=lookup,
        recursion_depth=recursion_depth,
        prune_unique=prune_unique,
        node_filter=cnudie.iter.Filter.resources,
        component_filter=component_filter,
        reftype_filter=reftype_filter,
    )
