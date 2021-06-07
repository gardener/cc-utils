import logging
import tarfile
import typing

import github3.repos
import dacite

import dso.labels
import dso.model
import gci.componentmodel as cm
import whitesource.filters
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
    components: typing.Generator[dso.model.ScanArtifact, None, None],
    filters: typing.List[whitesource.model.WhiteSourceFilterCfg],
) -> typing.Generator:

    components = whitesource.filters.apply_filters(components=list(components), filters=filters)

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
                raise NotImplementedError(ws_hint)


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
