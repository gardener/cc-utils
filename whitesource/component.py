import logging
import re
import tarfile
import typing

import github3.repos
import dacite

import dso.labels
import dso.model
import gci.componentmodel as cm
import whitesource.model


logger = logging.getLogger(__name__)


def _get_ws_label_from_artifact(source: cm.ComponentSource) -> dso.labels.SourceIdHint:
    if label := source.find_label(dso.labels.ScanLabelName.SOURCE_ID.value):
        return dacite.from_dict(
            data_class=dso.labels.SourceIdHint,
            data=label.value,
            config=dacite.Config(cast=[dso.labels.ScanPolicy]),
        )


def get_scan_artifacts_from_components(
    components: typing.Generator[typing.Union[tuple, typing.Any], typing.Any, typing.Any],
    filters: typing.List[whitesource.model.WhiteSourceFilterCfg],
) -> typing.Generator:

    components = apply_filters(components=components, filters=filters)

    for component in components:
        for artifact in component.sources + component.resources:

            if not artifact.access:
                logger.info(f'skipping {artifact.name=} since no access is found')
                continue
            if artifact.access.type not in (cm.AccessType.GITHUB, cm.AccessType.OCI_REGISTRY):
                logger.info(f'skipping {artifact.name=}, {artifact.access.type} is not supported')
                continue

            ws_hint = _get_ws_label_from_artifact(artifact)
            if not ws_hint or ws_hint.policy is dso.labels.ScanPolicy.SCAN:
                yield dso.model.ScanArtifact(
                    access=artifact.access,
                    label=ws_hint,
                    name=f'{component.name}:{component.version}/'
                         f'{"source" if artifact in component.sources else "resources"}/'
                         f'{artifact.name}:{artifact.version}',
                )
            elif ws_hint.policy is dso.labels.ScanPolicy.SKIP:
                continue
            else:
                raise NotImplementedError


def download_component(
    logger,
    github_repo: github3.repos.repo.Repository,
    path_filter_func: typing.Callable,
    ref: str,
    target: typing.IO,
):
    url = github_repo._build_url(
        'tarball',
        ref,
        base_url=github_repo._api,
    )

    files_to_scan = 0
    filtered_out_files = 0

    with tarfile.open(fileobj=target, mode='w|') as tar_out, \
        github_repo._get(url, allow_redirects=True, stream=True,) as res, \
        tarfile.open(fileobj=res.raw, mode='r|*') as src:

        res.raise_for_status()
        # valid because first tar entry is root directory and has no trailing \
        component_filename = src.next().name
        path_offset = len(component_filename) + 1

        for tar_info in src:
            if path_filter_func(tar_info.name[path_offset:]):
                tar_out.addfile(tarinfo=tar_info, fileobj=src.fileobj)
                files_to_scan += 1
            else:
                filtered_out_files += 1

    logger.info(f'{files_to_scan=}, {filtered_out_files=}')


def apply_filters(
    components: typing.Generator[typing.Union[tuple, typing.Any], typing.Any, typing.Any],
    filters: typing.List[whitesource.model.WhiteSourceFilterCfg],
) -> typing.Generator[typing.Union[tuple, typing.Any], typing.Any, typing.Any]:

    component_filter = None
    source_filter = None
    resource_filter = None

    for f in filters:
        if f.type == 'component':
            component_filter = f
        elif f.type == 'source':
            source_filter = f
        elif f.type == 'resource':
            resource_filter = f
        else:
            raise NotImplementedError

    if component_filter:
        components = _apply_component_filter(filter=component_filter, components=components)

    if source_filter:
        components = _apply_artifact_filter(filter=source_filter, components=components)

    if resource_filter:
        components = _apply_artifact_filter(filter=resource_filter, components=components)

    return components


def _apply_artifact_filter(
    filter: whitesource.model.WhiteSourceFilterCfg,
    components: typing.Generator[typing.Union[tuple, typing.Any], typing.Any, typing.Any],
):
    logger.debug(f'{filter=}')
    for c in components:
        # "source" or "resource", validated earlier
        artifacts = c.sources if filter.type == 'source' else c.resources

        logger.debug(f'pre filter ({filter.type=}): {len(artifacts)} artifacts')

        if isinstance(m := filter.match, bool):
            artifacts = list(_apply_artifact_action(
                action=filter.action,
                artifacts=artifacts, match=m,
            ))
            if filter.type == 'source':
                c.sources = artifacts
            elif filter.type == 'resource':
                c.resources = artifacts
            logger.debug(f'past filter ({filter.type=}): {len(artifacts)} artifacts')
            yield c

        elif isinstance(m := filter.match, dict):
            tmp = []
            for a in artifacts:
                if isinstance(a.type, cm.ResourceType):
                    resource_type_name = a.type.value
                elif isinstance(a.type, str):
                    resource_type_name = str(a.type)
                else:
                    raise NotImplementedError

                logger.debug(f'parsed {a.type} to {resource_type_name=}')

                if filter.action == 'include':
                    if re.search(
                            pattern=m.get('type') if filter.type == 'resource' else m.get('name'),
                            string=resource_type_name if filter.type == 'resource' else a.name,
                    ):
                        tmp.append(a)
                elif filter.action == 'exclude':
                    if not re.search(
                            pattern=m.get('type') if filter.type == 'resource' else m.get('name'),
                            string=resource_type_name if filter.type == 'resource' else a.name,
                    ):
                        tmp.append(a)
            if filter.type == 'source':
                c.sources = tmp
            elif filter.type == 'resource':
                c.resources = tmp
            logger.debug(f'past filter ({filter.type=}): {len(artifacts)} artifacts')
            yield c
        else:
            raise NotImplementedError


def _apply_artifact_action(
    action: str,
    artifacts,
    match: bool,
):
    if action == 'include':
        return artifacts if match else ()
    elif action == 'exclude':
        return artifacts if not match else ()
    else:
        raise RuntimeError


def _apply_component_filter(
    filter: whitesource.model.WhiteSourceFilterCfg,
    components: typing.Generator[typing.Union[tuple, typing.Any], typing.Any, typing.Any],
):
    if isinstance(m := filter.match, bool):
        yield from _apply_components_action(action=filter.action, components=components, match=m)

    elif isinstance(m := filter.match, dict):
        for c in components:
            if filter.action == 'include':
                if re.search(pattern=m.get('name'), string=c.name):
                    yield c
            elif filter.action == 'exclude':
                if not re.search(pattern=m.get('name'), string=c.name):
                    yield c
    else:
        raise NotImplementedError


def _apply_components_action(
    action: str,
    components,
    match: bool,
):
    if action == 'include':
        return components if match else ()
    elif action == 'exclude':
        return components if not match else ()
    else:
        raise RuntimeError
