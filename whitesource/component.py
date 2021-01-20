import tarfile
import typing

import github3.repos
import dacite

import dso.labels
import dso.model

import gci.componentmodel as cm


def _get_ws_label_from_source(source: cm.ComponentSource) -> dso.labels.SourceIdHint:
    if label := source.find_label(dso.labels.ScanLabelName.SOURCE_ID.value):
        return dacite.from_dict(
            data_class=dso.labels.SourceIdHint,
            data=label.value,
            config=dacite.Config(cast=[dso.labels.ScanPolicy]),
        )


def get_scan_artifacts_from_components(
    components: typing.Sequence[cm.Component],
) -> typing.Generator:
    for component in components:
        for source in component.sources:
            if source.type is not cm.SourceType.GIT:
                raise NotImplementedError

            if source.access.type is not cm.AccessType.GITHUB:
                raise NotImplementedError

            ws_hint = _get_ws_label_from_source(source)

            if not ws_hint or ws_hint.policy is dso.labels.ScanPolicy.SCAN:
                yield dso.model.ScanArtifact(
                    access=source.access,
                    label=ws_hint,
                    name=f'{component.name}_{source.identity(component.sources)}'
                )
            elif ws_hint.policy is dso.labels.ScanPolicy.SKIP:
                continue
            else:
                raise NotImplementedError


def download_component(
    clogger,
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

    clogger.info(f'{files_to_scan=}, {filtered_out_files=}')
    tar_out_size = target.tell()

    return tar_out_size
