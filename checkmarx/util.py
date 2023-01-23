import concurrent.futures
import datetime
from dateutil import parser
import functools
import logging
import shutil
import tarfile
import tempfile
import typing
import zipfile

import github3.exceptions
import github3.repos

import ccc.delivery
import ccc.github
import cnudie.retrieve
import checkmarx.client
import checkmarx.model as model
import checkmarx.project
import checkmarx.tablefmt
import ci.util
import github.compliance.model
import model.checkmarx as cmmmodel
import product.util
import reutil
import dso.labels
import dso.model

import gci.componentmodel as cm
import github.compliance.model as gcm

logger = logging.getLogger(__name__)


def scan_sources(
    component_descriptor: cm.ComponentDescriptor,
    cx_client: checkmarx.client.CheckmarxClient,
    team_id: str,
    timeout_seconds: int,
    max_workers: int = 4, # only two scan will be run per user
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
    force: bool = False,
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
        force=force,
        timeout_seconds=timeout_seconds,
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

            cx_label = source.find_label(name=dso.labels.SourceScanLabel.name)
            if not cx_label:
                cx_label = component.find_label(name=dso.labels.SourceScanLabel.name)
            if cx_label:
                cx_label: dso.labels.SourceScanLabel = dso.labels.deserialise_label(label=cx_label)
                scan_policy = cx_label.value.policy
            else:
                scan_policy = dso.labels.ScanPolicy.SCAN

            if scan_policy is dso.labels.ScanPolicy.SKIP:
                logger.info('Note: No source scanning is configured according to ScanPolicy label')
                continue
            elif scan_policy is dso.labels.ScanPolicy.SCAN:
                pass # do scan
            else:
                raise NotImplementedError(scan_policy)

            source_project_label = source.find_label(
                name=dso.labels.SourceProjectLabel.name
            )

            if source_project_label:
                source_project_label = dso.labels.deserialise_label(label=source_project_label)
                source_project_label: dso.labels.SourceProjectLabel

                scan_artifact_name = source_project_label.value
            else:
                scan_artifact_name = None

            if not scan_artifact_name:
                scan_artifact_name = source.name

            logger.info(f'Using project name from cd-label {scan_artifact_name}')

            yield dso.model.ScanArtifact(
                source=source,
                name=scan_artifact_name,
                label=cx_label,
                component=component,
            )


def scan_artifacts(
    cx_client: checkmarx.client.CheckmarxClient,
    max_workers: int,
    scan_artifacts: typing.Tuple[dso.model.ScanArtifact],
    team_id: str,
    timeout_seconds: int,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
    force: bool = False,
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
        force=force,
        timeout_seconds=timeout_seconds,
    )

    def init_scan(
        scan_artifact: dso.model.ScanArtifact,
    ) -> model.ScanResult:
        nonlocal cx_client
        nonlocal scan_func
        nonlocal team_id

        cx_project = checkmarx.project.init_checkmarx_project(
            checkmarx_client=cx_client,
            source_name=scan_artifact.name,
            team_id=team_id,
        )

        if scan_artifact.source.access.type is cm.AccessType.GITHUB:
            return scan_func(
                cx_project=cx_project,
                scan_artifact=scan_artifact,
            )
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
            scan_result: model.ScanResult

            if scan_result.scan_succeeded:
                finished_scans.scans.append(scan_result)
            else:
                finished_scans.failed_scans.append(scan_result)

            finished += 1
            logger.info(f'remaining: {artifact_count - finished}')

    return finished_scans


def upload_and_scan_gh_artifact(
    artifact: dso.model.ScanArtifact,
    gh_repo: github3.repos.repo.Repository,
    cx_project: checkmarx.project.CheckmarxProject,
    path_filter_func: typing.Callable,
    source_commit_hash: str,
    force: bool,
    timeout_seconds: int,
) -> model.ScanResult:

    clogger = component_logger(artifact_name=artifact.name)

    last_scans = cx_project.get_last_scans()
    tm_start = datetime.datetime.now()
    if len(last_scans) < 1:
        clogger.info('no scans found in project history. '
                     f'Starting new scan hash={source_commit_hash}')
        scan_id = download_repo_and_create_scan(
            artifact_name=artifact.name,
            artifact_version=artifact.source.version,
            cx_project=cx_project,
            hash=source_commit_hash,
            repo=gh_repo,
            path_filter_func=path_filter_func,
        )
    else:
        last_scan = last_scans[0]
        scan_id = last_scan.id

        if cx_project.is_scan_finished(last_scan):
            clogger.info('no running scan found. Comparing hashes')
            if force or cx_project.is_scan_necessary(hash=source_commit_hash):
                clogger.info('current hash differs from remote hash in cx. '
                            f'New scan started for hash={source_commit_hash}')
                scan_id = download_repo_and_create_scan(
                    artifact_name=artifact.name,
                    artifact_version=artifact.source.version,
                    cx_project=cx_project,
                    hash=source_commit_hash,
                    repo=gh_repo,
                    path_filter_func=path_filter_func,
                )
            else:
                clogger.info('version/hash has already been scanned. Getting results of last scan')
        else:
            clogger.info(f'found a running scan id={last_scan.id}. Polling it')
            start_time = parser.parse(last_scan.dateAndTime.startedOn)
            # ignore if older than two hours, sometimes scans keep hanging and never end
            if (datetime.datetime.now() - start_time).total_seconds() > 2 * 60 * 60:
                clogger.info(f'running scan id={last_scan.id} found but older than two hours. '
                    f'Starting new scan hash={source_commit_hash}')
                scan_id = download_repo_and_create_scan(
                    artifact_name=artifact.name,
                    artifact_version=artifact.source.version,
                    cx_project=cx_project,
                    hash=source_commit_hash,
                    repo=gh_repo,
                    path_filter_func=path_filter_func,
                )

    scan_result = cx_project.poll_and_retrieve_scan(
        scan_id=scan_id,
        component=artifact.component,
        source=artifact.source,
        timeout_seconds=timeout_seconds,
    )
    duration = datetime.datetime.now() - tm_start
    clogger.info(f'Scan for component {artifact.name} took: {duration}')
    return scan_result


def scan_gh_artifact(
    cx_project: checkmarx.project.CheckmarxProject,
    scan_artifact: dso.model.ScanArtifact,
    timeout_seconds: int,
    exclude_paths: typing.Sequence[str] = (),
    include_paths: typing.Sequence[str] = (),
    force: bool = False,
) -> model.ScanResult:

    github_api = ccc.github.github_api_from_gh_access(access=scan_artifact.source.access)

    # access type has to be github thus we can call these methods
    try:
        gh_repo = github_api.repository(
            owner=scan_artifact.source.access.org_name(),
            repository=scan_artifact.source.access.repository_name(),
        )
    except Exception as e:
        logger.error(f'Failed to access Github repository, {scan_artifact.source.access.org_name()},'
            f'{scan_artifact.source.access.repository_name()}, on: '
            f'{scan_artifact.source.access.hostname()}')
        raise e

    try:
        commit_hash = product.util.guess_commit_from_source(
            artifact_name=scan_artifact.name,
            commit_hash=scan_artifact.source.access.commit,
            github_repo=gh_repo,
            ref=scan_artifact.source.access.ref,
        )
    except github3.exceptions.NotFoundError as e:
        raise product.util.RefGuessingFailedError(e)

    if scan_artifact.label is not None:
        if scan_artifact.label.value.path_config is not None:
            path_config = scan_artifact.label.value.path_config
            include_paths = set((*include_paths, *path_config.include_paths))
            exclude_paths = set((*exclude_paths, *path_config.exclude_paths))

    # if the scan_artifact has no label we will implicitly scan everything
    # since all images have to specify a label in order to be scanned
    # only github access types can occour here without the label
    path_filter_func = reutil.re_filter(
        include_regexes=include_paths,
        exclude_regexes=exclude_paths,
    )
    return upload_and_scan_gh_artifact(
        artifact=scan_artifact,
        cx_project=cx_project,
        gh_repo=gh_repo,
        source_commit_hash=commit_hash,
        path_filter_func=path_filter_func,
        force=force,
        timeout_seconds=timeout_seconds,
    )


def greatest_severity(result: model.ScanResult) -> model.Severity | None:
    if not result.scan_statistic:
        raise RuntimeError('must only be called for successful scans')

    if result.scan_statistic.highSeverity > 0:
        return model.Severity.HIGH
    elif result.scan_statistic.mediumSeverity > 0:
        return model.Severity.MEDIUM
    elif result.scan_statistic.lowSeverity > 0:
        return model.Severity.LOW
    elif result.scan_statistic.infoSeverity > 0:
        return model.Severity.INFO
    else:
        return None


def checkmarx_severity_to_github_severity(severity: model.Severity) -> gcm.Severity:
    if not severity:
        raise RuntimeError('must only be called for successful scans')

    if severity in (
        model.Severity.HIGH,
        model.Severity.MEDIUM,
    ):
        return gcm.Severity.BLOCKER
    elif severity is model.Severity.LOW:
        return gcm.Severity.LOW
    elif severity is model.Severity.INFO:
        return None
    else:
        raise NotImplementedError(severity)


def print_scans(
    scans: model.FinishedScans,
    threshold: model.Severity,
    routes: checkmarx.client.CheckmarxRoutes,
):
    scans_above_threshold = [s for s in scans.scans
        if greatest_severity(s) and greatest_severity(s) >= threshold]
    scans_below_threshold = [s for s in scans.scans
        if greatest_severity(s) and greatest_severity(s) < threshold]

    if scans_above_threshold:
        print('\n')
        logger.info('critical scans above threshold')
        checkmarx.tablefmt.print_scan_result(
            scan_results=scans_above_threshold,
            routes=routes,
        )
    else:
        logger.info('no critical artifacts above threshold')

    if scans_below_threshold:
        print('\n')
        logger.info('clean scans below threshold')
        checkmarx.tablefmt.print_scan_result(
            scan_results=scans_below_threshold,
            routes=routes,
        )
    else:
        logger.info('no scans below threshold')

    if scans.failed_scans:
        print('\n')
        failed_artifacts_str = '\n'.join(
            (
                f'- {scan_result.artifact_name}:{scan_result.scanned_element.source.version}'
                for scan_result in scans.failed_scans
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
    artifact_version: str,
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
    cx_project.project_details.set_custom_field(
        attribute_key=model.CustomFieldKeys.VERSION,
        value=artifact_version,
    )
    cx_project.update_remote_project()

    scan_settings = model.ScanSettings(
        projectId=cx_project.project_details.id,
        comment=f'Scanning artifact name: {artifact_name}, version: {artifact_version}, '
            f'commit: {hash}'
    )
    scan_id = cx_project.start_scan(scan_settings)
    clogger.info(f'started scan id={scan_id} project id={cx_project.project_details.id}')

    return scan_id


@functools.lru_cache
def component_logger(artifact_name: str):
    return logging.getLogger(artifact_name)


def get_checkmarx_cfg(checkmarx_cfg_name: str) -> cmmmodel.CheckmarxConfig:
    cfg_fac = ci.util.ctx().cfg_factory()
    return cfg_fac.checkmarx(checkmarx_cfg_name)


@functools.lru_cache()
def create_checkmarx_client(checkmarx_cfg: cmmmodel.CheckmarxConfig):
    return checkmarx.client.CheckmarxClient(checkmarx_cfg)


def iter_artefact_metadata(results: typing.Iterable[model.ScanResult]) \
    -> typing.Generator[dso.model.ArtefactMetadata, None, None]:
    for result in results:
        artefact = github.compliance.model.artifact_from_node(result.scanned_element)
        artefact_ref = dso.model.component_artefact_id_from_ocm(
            component=result.scanned_element.component,
            artefact=artefact,
        )
        meta = dso.model.Metadata(
            datasource=dso.model.Datasource.CHECKMARX,
            type=dso.model.Datatype.CODECHECKS_AGGREGATED,
            creation_date=datetime.datetime.now(),
        )
        codecheck = dso.model.CodecheckSummary(
            findings=dso.model.CodecheckFindings(
                high=result.scan_statistic.highSeverity,
                medium=result.scan_statistic.mediumSeverity,
                low=result.scan_statistic.lowSeverity,
                info=result.scan_statistic.infoSeverity,
            ),
            risk_rating=result.scan_response.scanRisk,
            risk_severity=result.scan_response.scanRiskSeverity,
            overview_url=result.overview_url,
            report_url=result.report_url,
        )

        yield dso.model.ArtefactMetadata(
            artefact=artefact_ref,
            meta=meta,
            data=codecheck,
        )
