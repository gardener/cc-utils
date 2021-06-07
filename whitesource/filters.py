import logging
import re
import typing

import gci.componentmodel as cm
import whitesource.model


logger = logging.getLogger(__name__)


def _print_filter_stats(
    components: typing.List[cm.Component],
    filtered: bool,
):
    sources = len([r for c in components for r in c.sources])
    resources = len([r for c in components for r in c.resources])
    logger.info(
        f'{"past" if filtered else "pre"} filter: '
        f'{len(components)} component with {sources} sources and {resources} resources'
    )


def apply_filters(
    components: typing.List[cm.Component],
    filters: typing.List[whitesource.model.WhiteSourceFilterCfg],
) -> typing.Generator:

    _print_filter_stats(components=components, filtered=False)
    for f in filters:
        if f.type is whitesource.model.FilterType.COMPONENT:
            components = [c for c in filter(
                lambda c: _component_filter(filter_cfg=f,component=c),
                components,
            )]
        elif (
            f.type is whitesource.model.FilterType.SOURCE
            or f.type is whitesource.model.FilterType.RESOURCE
        ):
            if not components:
                logger.warning('all components excluded, skipping artifact filter')
            else:
                components = list(_apply_artifact_filter(filter_cfg=f, components=components))

        else:
            raise NotImplementedError(f.type)

    _print_filter_stats(components=components, filtered=True)
    yield from components


def _apply_artifact_filter(
    filter_cfg: whitesource.model.WhiteSourceFilterCfg,
    components: typing.Generator[cm.Component, None, None],
):
    logger.debug('applying artifact filter')
    for c in components:
        artifacts_pre = (
            c.sources if filter_cfg.type is whitesource.model.FilterType.SOURCE
            else c.resources
        )
        filtered_artifacts = [c for c in filter(
            lambda a: _artifact_filter(filter_cfg=filter_cfg, artifact=a),
            artifacts_pre,
        )]
        if filter_cfg.type is whitesource.model.FilterType.SOURCE:
            c.sources = filtered_artifacts
        elif filter_cfg.type is whitesource.model.FilterType.RESOURCE:
            c.resources = filtered_artifacts
        else:
            raise NotImplementedError(filter_cfg.type)

        yield c


def _artifact_filter(
    filter_cfg: whitesource.model.WhiteSourceFilterCfg,
    artifact: typing.Union[cm.ComponentSource, cm.Resource],
):
    if isinstance(filter_cfg.match, bool):
        return _match_bool_filter(filter_cfg=filter_cfg, artifact=artifact)

    elif isinstance(filter_cfg.match, dict):
        # enum to string
        if isinstance(artifact.type, cm.ResourceType):
            resource_type_name = artifact.type.value
        elif isinstance(artifact.type, str):
            resource_type_name = str(artifact.type)
        else:
            raise NotImplementedError

        re_str = (
            resource_type_name if filter_cfg.type is whitesource.model.FilterType.RESOURCE
            else artifact.name
        )
        re_pat = (
            filter_cfg.match.get('type') if filter_cfg.type is whitesource.model.FilterType.RESOURCE
            else filter_cfg.match.get('name')
        )

        if filter_cfg.action is whitesource.model.ActionType.INCLUDE:
            if re.search(pattern=re_pat, string=re_str):
                logger.debug(
                    f'included {artifact.name=}, '
                    f'reason: {re_str=} matches {re_pat=} and {filter_cfg.action=}'
                )
                return True
        elif filter_cfg.action is whitesource.model.ActionType.EXCLUDE:
            if not re.search(pattern=re_pat, string=re_str):
                logger.debug(
                    f'included {artifact.name=}, reason: '
                    f'{re_str=} does not match {re_pat=} and {filter_cfg.action=}'
                )
                return True
        else:
            raise NotImplementedError(filter_cfg.action)
    else:
        raise NotImplementedError(filter_cfg.match)

    logger.debug(f'excluded {artifact.name=}')
    return False


def _match_bool_filter(
    filter_cfg: whitesource.model.WhiteSourceFilterCfg,
    artifact: typing.Union[cm.Resource, cm.ComponentSource] = None,
    component: cm.Component = None,
) -> bool:

    if not artifact and not component:
        raise RuntimeError('One of "artifact" or "component" must be given')

    if filter_cfg.action is whitesource.model.ActionType.INCLUDE:
        if filter_cfg.match:
            logger.debug(f'included {artifact.name if artifact else component.name}, '
                         f'reason: {filter_cfg.match=} and {filter_cfg.action=}')
            return True
    elif filter_cfg.action is whitesource.model.ActionType.EXCLUDE:
        if not filter_cfg.match:
            logger.debug(f'included {artifact.name if artifact else component.name}, '
                         f'reason: {filter_cfg.match=} and {filter_cfg.action=}')
            return True
    else:
        raise NotImplementedError(filter_cfg.action)

    logger.debug(f'excluded {artifact.name if artifact else component.name}')
    return False


def _component_filter(
    filter_cfg: whitesource.model.WhiteSourceFilterCfg,
    component: cm.Component,
) -> bool:
    logger.debug('applying component filter')
    if isinstance(filter_cfg.match, bool):
        return _match_bool_filter(filter_cfg=filter_cfg, component=component)

    elif isinstance(filter_cfg.match, dict):
        if filter_cfg.action is whitesource.model.ActionType.INCLUDE:
            if re.search(pattern=filter_cfg.match.get('name'), string=component.name):
                return True
        elif filter_cfg.action is whitesource.model.ActionType.EXCLUDE:
            if not re.search(pattern=filter_cfg.match.get('name'), string=component.name):
                return True
    else:
        raise NotImplementedError

    return False
