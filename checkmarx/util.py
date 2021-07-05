import concurrent.futures
import functools
import logging
import shutil
import tarfile
import tempfile
import traceback
import typing
import zipfile

import dacite
import github3.exceptions
import github3.repos

import ccc.github
import cnudie.retrieve
import checkmarx.client
import checkmarx.model as model
import checkmarx.project
import checkmarx.tablefmt
import ci.util
import mailutil
import product.util
import reutil
import dso.labels
import dso.model

import gci.componentmodel as cm


logger = logging.getLogger(__name__)


def scan_sources(
    component_descriptor: cm.ComponentDescriptor,
    cx_client: checkmarx.client.CheckmarxClient,
    team_id: str,
    threshold: int,
    max_workers: int = 4, # only two scan will be run per user
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
) -> model.FinishedScans:

    components = tuple(cnudie.retrieve.components(component=component_descriptor))

    # identify scan artifacts and collect them in a sequence
    artifacts_gen = _get_scan_artifacts_from_components(
        components=components,
    )

    return scan_artifacts(
        cx_client=cx_client,
        exclude_paths=exclude_paths,
        include_paths=include_paths,
        max_workers=max_workers,
        scan_artifacts=artifacts_gen,
        team_id=team_id,
        threshold=threshold,
    )


def _get_scan_artifacts_from_components(
    components: typing.List[cm.Component],
) -> typing.Generator:
    for component in components:
        for source in component.sources:
            if source.type is not cm.SourceType.GIT:
                raise NotImplementedError

            if source.access.type is not cm.AccessType.GITHUB:
                raise NotImplementedError

            cx_label = get_source_scan_label_from_labels(source.labels)

            if not cx_label or cx_label.policy is dso.labels.ScanPolicy.SCAN:
                yield dso.model.ScanArtifact(
                    access=source.access,
                    name=f'{component.name}_{source.identity(peers=component.sources)}',
                    label=cx_label,
                )
            elif cx_label.policy is dso.labels.ScanPolicy.SKIP:
                continue
            else:
                raise NotImplementedError


scan_label_names = set(item.value for item in dso.labels.ScanLabelName)


def get_source_scan_label_from_labels(labels: typing.Sequence[cm.Label]):
    global scan_label_names
    for label in labels:
        if label.name in scan_label_names:
            if dso.labels.ScanLabelName(label.name) is dso.labels.ScanLabelName.SOURCE_SCAN:
                return dacite.from_dict(
                    dso.labels.SourceScanHint,
                    data=label.value,
                    config=dacite.Config(cast=[dso.labels.ScanPolicy])
                )


def scan_artifacts(
    cx_client: checkmarx.client.CheckmarxClient,
    max_workers: int,
    scan_artifacts: typing.Tuple[dso.model.ScanArtifact],
    team_id: str,
    threshold: int,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
) -> model.FinishedScans:

    finished_scans = model.FinishedScans()

    artifacts = tuple(scan_artifacts)
    artifact_count = len(artifacts)
    finished = 0

    logger.info(f'will scan {artifact_count} artifacts')

    scan_func = functools.partial(
        scan_gh_artifact,
        exclude_paths=exclude_paths,
        include_paths=include_paths,
    )

    def init_scan(
        scan_artifact: dso.model.ScanArtifact,
    ) -> typing.Union[model.ScanResult, model.FailedScan]:
        nonlocal cx_client
        nonlocal scan_func
        nonlocal team_id

        cx_project = checkmarx.project.init_checkmarx_project(
            checkmarx_client=cx_client,
            source_name=scan_artifact.name,
            team_id=team_id,
        )

        if scan_artifact.access.type is cm.AccessType.GITHUB:
            try:
                return scan_func(
                    cx_project=cx_project,
                    scan_artifact=scan_artifact,
                )
            except:
                traceback.print_exc()
                return model.FailedScan(scan_artifact.name)
        else:
            raise NotImplementedError

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for artifact in artifacts:
            futures.append(
                executor.submit(init_scan, artifact)
            )
        for future in concurrent.futures.as_completed(futures):
            scan_result = future.result()
            if isinstance(scan_result, model.FailedScan):
                finished_scans.failed_scans.append(scan_result.artifact_name)
            else:
                if scan_result.scan_response.scanRiskSeverity > threshold:
                    finished_scans.scans_above_threshold.append(scan_result)
                else:
                    finished_scans.scans_below_threshold.append(scan_result)

            finished += 1
            logger.info(f'remaining: {artifact_count - finished}')

    return finished_scans


def upload_and_scan_gh_artifact(
    artifact_name: str,
    gh_repo: github3.repos.repo.Repository,
    cx_project: checkmarx.project.CheckmarxProject,
    path_filter_func: typing.Callable,
    source_commit_hash,
) -> model.ScanResult:

    clogger = component_logger(artifact_name=artifact_name)

    last_scans = cx_project.get_last_scans()

    if len(last_scans) < 1:
        clogger.info('no scans found in project history. '
                     f'Starting new scan hash={source_commit_hash}')
        scan_id = download_repo_and_create_scan(
            artifact_name=artifact_name,
            cx_project=cx_project,
            hash=source_commit_hash,
            repo=gh_repo,
            path_filter_func=path_filter_func,
        )
        return cx_project.poll_and_retrieve_scan(scan_id=scan_id)

    last_scan = last_scans[0]
    scan_id = last_scan.id

    if cx_project.is_scan_finished(last_scan):
        clogger.info('no running scan found. Comparing hashes')

        if cx_project.is_scan_necessary(hash=source_commit_hash):
            clogger.info('current hash differs from remote hash in cx. '
                         f'New scan started for hash={source_commit_hash}')
            scan_id = download_repo_and_create_scan(
                artifact_name=artifact_name,
                cx_project=cx_project,
                hash=source_commit_hash,
                repo=gh_repo,
                path_filter_func=path_filter_func,
            )
        else:
            clogger.info('version of hash has already been scanned. Getting results of last scan')
    else:
        clogger.info(f'found a running scan id={last_scan.id}. Polling it')

    return cx_project.poll_and_retrieve_scan(scan_id=scan_id)


def scan_gh_artifact(
    cx_project: checkmarx.project.CheckmarxProject,
    scan_artifact: dso.model.ScanArtifact,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
) -> model.ScanResult:

    github_api = ccc.github.github_api_from_gh_access(access=scan_artifact.access)

    # access type has to be github thus we can call these methods
    gh_repo = github_api.repository(
        owner=scan_artifact.access.org_name(),
        repository=scan_artifact.access.repository_name(),
    )
    try:
        commit_hash = product.util.guess_commit_from_source(
            artifact_name=scan_artifact.name,
            commit_hash=scan_artifact.access.commit,
            github_repo=gh_repo,
            ref=scan_artifact.access.ref,
        )
    except github3.exceptions.NotFoundError as e:
        raise product.util.RefGuessingFailedError(e)

    if scan_artifact.label is not None:
        if scan_artifact.label.path_config is not None:
            include_paths = set((*include_paths, *scan_artifact.label.path_config.include_paths))
            exclude_paths = set((*exclude_paths, *scan_artifact.label.path_config.exclude_paths))

    # if the scan_artifact has no label we will implicitly scan everything
    # since all images have to specify a label in order to be scanned
    # only github access types can occour here without the label
    path_filter_func = reutil.re_filter(
        include_regexes=include_paths,
        exclude_regexes=exclude_paths,
    )
    return upload_and_scan_gh_artifact(
        artifact_name=scan_artifact.name,
        cx_project=cx_project,
        gh_repo=gh_repo,
        source_commit_hash=commit_hash,
        path_filter_func=path_filter_func,
    )


def send_mail(
    email_recipients,
    routes: checkmarx.client.CheckmarxRoutes,
    scans: model.FinishedScans,
    threshold: int,
):
    body = checkmarx.tablefmt.assemble_mail_body(
        failed_artifacts=scans.failed_scans,
        routes=routes,
        scans_above_threshold=scans.scans_above_threshold,
        scans_below_threshold=scans.scans_below_threshold,
        threshold=threshold,
    )
    try:
        # get standard cfg set for email cfg
        default_cfg_set_name = ci.util.current_config_set_name()
        cfg_factory = ci.util.ctx().cfg_factory()
        cfg_set = cfg_factory.cfg_set(default_cfg_set_name)

        logger.info(f'sending notification emails to: {",".join(email_recipients)}')
        mailutil._send_mail(
            email_cfg=cfg_set.email(),
            recipients=email_recipients,
            mail_template=body,
            subject='[Action Required] checkmarx vulnerability report',
            mimetype='html',
        )
        logger.info('sent notification emails to: ' + ','.join(email_recipients))

    except Exception:
        traceback.print_exc()
        logger.warning('error whilst trying to send notification-mail')


def print_scans(
    scans: model.FinishedScans,
    routes: checkmarx.client.CheckmarxRoutes,
):
    if scans.scans_above_threshold:
        print('\n')
        logger.info('critical scans above threshold')
        checkmarx.tablefmt.print_scan_result(
            scan_results=scans.scans_above_threshold,
            routes=routes,
        )
    else:
        logger.info('no critical artifacts above threshold')

    if scans.scans_below_threshold:
        print('\n')
        logger.info('clean scans below threshold')
        checkmarx.tablefmt.print_scan_result(
            scan_results=scans.scans_below_threshold,
            routes=routes,
        )
    else:
        logger.info('no scans below threshold')

    if scans.failed_scans:
        print('\n')
        failed_artifacts_str = '\n'.join(
            (
                f'- {artifact_name}' for artifact_name in scans.failed_scans
            )
        )
        logger.warning(f'failed scan artifacts:\n\n{failed_artifacts_str}\n')


def _download_and_zip_repo(
    clogger,
    path_filter_func: typing.Callable,
    ref: str,
    repo: github3.repos.repo.Repository,
    tmp_file
):
    files_to_scan = 0
    filtered_out_files = 0

    url = repo._build_url('tarball', ref, base_url=repo._api)
    with repo._get(url, stream=True) as r, \
        tarfile.open(fileobj=r.raw, mode='r|*') as tar, \
        zipfile.ZipFile(tmp_file, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:

        r.raise_for_status()
        max_octets = 128 * 1024 * 1024  # 128 MiB

        # valid because first tar entry is root directory and has no trailing \
        path_offset = len(tar.next().name) + 1
        for tar_info in tar:
            if path_filter_func(arcname := tar_info.name[path_offset:]):
                if tar_info.isfile():
                    files_to_scan += 1
                    src = tar.extractfile(tar_info)
                    if tar_info.size <= max_octets:
                        zip_file.writestr(arcname, src.read())
                    else:
                        with tempfile.NamedTemporaryFile() as tmp:
                            shutil.copyfileobj(src, tmp)
                            zip_file.write(filename=tmp.name, arcname=arcname)
            else:
                filtered_out_files += 1

    clogger.info(f'{files_to_scan=}, {filtered_out_files=}')


def download_repo_and_create_scan(
    artifact_name: str,
    hash:str,
    cx_project: checkmarx.project.CheckmarxProject,
    path_filter_func: typing.Callable,
    repo: github3.repos.repo.Repository,
):
    clogger = component_logger(artifact_name=artifact_name)
    clogger.info('downloading sources from github')
    with tempfile.TemporaryFile() as tmp_file:
        _download_and_zip_repo(
            clogger=clogger,
            repo=repo,
            ref=hash,
            tmp_file=tmp_file,
            path_filter_func=path_filter_func,
        )
        tmp_file.seek(0)
        clogger.info('uploading sources to checkmarx')
        cx_project.upload_zip(file=tmp_file)

    cx_project.project_details.set_custom_field(
        attribute_key=model.CustomFieldKeys.HASH,
        value=hash,
    )
    cx_project.project_details.set_custom_field(
        attribute_key=model.CustomFieldKeys.COMPONENT_NAME,
        value=artifact_name,
    )

    cx_project.update_remote_project()
    scan_id = cx_project.start_scan()
    clogger.info(f'started scan id={scan_id} project id={cx_project.project_details.id}')

    return scan_id


@functools.lru_cache
def component_logger(artifact_name: str):
    return logging.getLogger(artifact_name)


@functools.lru_cache()
def create_checkmarx_client(checkmarx_cfg_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    return checkmarx.client.CheckmarxClient(cfg_fac.checkmarx(checkmarx_cfg_name))
