import ocm


def as_component(
    component: ocm.Component | ocm.ComponentDescriptor,
    /,
) -> ocm.Component:
    if isinstance(component, ocm.Component):
        return component
    if isinstance(component, ocm.ComponentDescriptor):
        return component.component

    raise ValueError(component)


def main_source(
    component: ocm.Component | ocm.ComponentDescriptor,
    *,
    no_source_ok: bool=True,
    ambiguous_ok: bool=True,
) -> ocm.Source | None:
    '''
    returns the "main source" of the given OCM Component. Typically, components will have exactly
    one source, in which the applied logic is to return the sole source-artefact.

    For other cases, behaviour can be controlled via kw-(only-)params:

    no_source_ok: if component has _no_ sources, return None
    ambiguous_ok: if component has more than one source, return first

    In cases where no main-source can be determined, raises ValueError.
    '''
    component = as_component(component)

    if len(component.sources) == 1:
        return component.sources[0]
    elif not component.sources:
        if no_source_ok:
            return None
        else:
            raise ValueError('no sources', component)

    if ambiguous_ok:
        return component.sources[0]

    raise ValueError('could not umambiguously determine main-source', component)
