import concurrent.futures
import functools
import logging
import shutil
import tarfile
import tempfile
import threading
import traceback
import typing
import zipfile

import checkmarx.client
import checkmarx.model as model
import checkmarx.project
import checkmarx.tablefmt
import ci.util
import mailutil
import product.util

import github3.exceptions

import ccc.github
import product.v2
import gci.componentmodel as cm


def upload_and_scan_repo(
    component: cm.Component,  # needs to remain at first position (currying)
    checkmarx_client: checkmarx.client.CheckmarxClient,
    team_id: str,
    path_filter_func: typing.Callable,
):

    cx_project = checkmarx.project.init_checkmarx_project(
        checkmarx_client=checkmarx_client,
        team_id=team_id,
        component=component,
    )

    clogger = component_logger(component_name=component.name)

    last_scans = cx_project.get_last_scans()

    try:
        commit_hash = product.util.guess_commit_from_ref(component=component)
    except github3.exceptions.NotFoundError as e:
        raise product.util.RefGuessingFailedError(e)

    github_api = ccc.github.github_api_from_component(component=component)
    github_repo = ccc.github.GithubRepo.from_component(component=component)
    repo = github_api.repository(
        github_repo.org_name,
        github_repo.repo_name,
    )

    if len(last_scans) < 1:
        clogger.info('No scans found in project history')
        scan_id = download_repo_and_create_scan(
            component_name=component.name,
            cx_project=cx_project,
            hash=commit_hash,
            repo=repo,
            path_filter_func=path_filter_func,
        )
        return cx_project.poll_and_retrieve_scan(scan_id=scan_id)

    last_scan = last_scans[0]
    scan_id = last_scan.id

    if cx_project.is_scan_finished(last_scan):
        clogger.info('No active scan found for component. Checking for hash')

        if cx_project.is_scan_necessary(hash=commit_hash):
            clogger.info('downloading repo')
            scan_id = download_repo_and_create_scan(
                component_name=component.name,
                cx_project=cx_project,
                hash=commit_hash,
                repo=repo,
                path_filter_func=path_filter_func,
            )
    else:
        clogger.info(
            f'scan with id: {last_scan.id} for component {component.name} '
            'already running. Polling last scan.'
        )

    return cx_project.poll_and_retrieve_scan(scan_id=scan_id)


def scan_sources(
    client: checkmarx.client.CheckmarxClient,
    team_id: str,
    component_descriptor_path: str,
    threshold: int,
    path_filter_func: typing.Callable = lambda x: True,
    max_workers: int = 8,
):
    component_descriptor = cm.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_path)
    )

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    scan_func = functools.partial(
        upload_and_scan_repo,
        checkmarx_client=client,
        team_id=team_id,
        path_filter_func=path_filter_func,
    )

    failed_sentinel = object()
    success_count = 0
    failed_count = 0
    components = tuple(product.v2.components(component_descriptor_v2=component_descriptor))
    components_count = len(components)
    failed_components = []
    lock = threading.Lock()

    def try_scanning(component: cm.Component):
        nonlocal failed_count
        nonlocal success_count
        nonlocal failed_sentinel
        nonlocal failed_components

        try:
            result = scan_func(component)
            lock.acquire()
            success_count += 1
            ci.util.info(f'remaining: {components_count - (success_count + failed_count)}')
            lock.release()
            return result
        except:
            lock.acquire()
            failed_count += 1
            ci.util.info(f'remaining: {components_count - (success_count + failed_count)}')
            lock.release()
            traceback.print_exc()
            failed_components.append(component.name)
            return failed_sentinel

    ci.util.info(f'will scan {components_count} component(s)')

    scan_results_above_threshold = []
    scan_results_below_threshold = []

    for scan_result in executor.map(try_scanning, components):
        if scan_result is not failed_sentinel:
            if scan_result.scan_response.scanRiskSeverity > threshold:
                scan_results_above_threshold.append(scan_result)
            else:
                scan_results_below_threshold.append(scan_result)

    return model.FinishedScans(
        scans_above_threshold=scan_results_above_threshold,
        scans_below_threshold=scan_results_below_threshold,
        failed_components=failed_components,
    )


def send_mail(
    scans: model.FinishedScans,
    threshold: int,
    email_recipients,
    routes: checkmarx.client.CheckmarxRoutes,
):
    body = checkmarx.tablefmt.assemble_mail_body(
        scans_above_threshold=scans.scans_above_threshold,
        scans_below_threshold=scans.scans_below_threshold,
        failed_components=scans.failed_components,
        threshold=threshold,
        routes=routes,
    )
    try:
        # get standard cfg set for email cfg
        default_cfg_set_name = ci.util.current_config_set_name()
        cfg_factory = ci.util.ctx().cfg_factory()
        cfg_set = cfg_factory.cfg_set(default_cfg_set_name)

        ci.util.info(f'sending notification emails to: {",".join(email_recipients)}')
        mailutil._send_mail(
            email_cfg=cfg_set.email(),
            recipients=email_recipients,
            mail_template=body,
            subject='[Action Required] checkmarx vulnerability report',
            mimetype='html',
        )
        ci.util.info('sent notification emails to: ' + ','.join(email_recipients))

    except Exception:
        traceback.print_exc()
        ci.util.warning('error whilst trying to send notification-mail')


def print_scans(
    scans: model.FinishedScans,
    routes: checkmarx.client.CheckmarxRoutes,
):
    # XXX raise if an error occurred?
    if scans.scans_above_threshold:
        print('\n')
        ci.util.info('Critical scans above threshold')
        checkmarx.tablefmt.print_scan_result(
            scan_results=scans.scans_above_threshold,
            routes=routes,
        )
    else:
        ci.util.info('no critical components above threshold found')

    if scans.scans_below_threshold:
        print('\n')
        ci.util.info('Clean scans below threshold')
        checkmarx.tablefmt.print_scan_result(
            scan_results=scans.scans_below_threshold,
            routes=routes,
        )
    else:
        ci.util.info('no scans below threshold to print')

    if scans.failed_components:
        print('\n')
        failed_components_str = '\n'.join(
            (
                component_name for component_name in scans.failed_components
            )
        )
        ci.util.info(f'failed components:\n{failed_components_str}')


def _download_and_zip_repo(repo, ref: str,path_filter_func: typing.Callable, tmp):
    url = repo._build_url('tarball', ref, base_url=repo._api)
    with repo._get(url, stream=True) as r, \
        tarfile.open(fileobj=r.raw, mode='r|*') as tar, \
        zipfile.ZipFile(tmp, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:

        r.raise_for_status()
        max_octets = 128 * 1024 * 1024  # 128 MiB

        # valid because first tar entry is root directory and has no trailing \
        path_offset = len(tar.next().name) + 1
        for tar_info in tar:
            if path_filter_func(arcname := tar_info.name[path_offset:]):
                if tar_info.isfile():
                    src = tar.extractfile(tar_info)
                    if tar_info.size <= max_octets:
                        zip_file.writestr(arcname, src.read())
                    else:
                        with tempfile.NamedTemporaryFile() as tmp_file:
                            shutil.copyfileobj(src, tmp_file)
                            zip_file.write(filename=tmp_file.name, arcname=arcname)


def download_repo_and_create_scan(
    component_name:str,
    hash:str,
    cx_project: checkmarx.project.CheckmarxProject,
    path_filter_func: typing.Callable,
    repo,
):
    clogger = component_logger(component_name)
    clogger.info('downloading sources')
    with tempfile.TemporaryFile() as tmp_file:
        _download_and_zip_repo(
            repo=repo,
            ref=hash,
            tmp=tmp_file,
            path_filter_func=path_filter_func,
        )
        tmp_file.seek(0)
        clogger.info('uploading sources')
        cx_project.upload_zip(file=tmp_file)

    cx_project.project_details.set_custom_field(
        attribute_key=model.CustomFieldKeys.HASH,
        value=hash,
    )
    cx_project.project_details.set_custom_field(
        attribute_key=model.CustomFieldKeys.COMPONENT_NAME,
        value=component_name,
    )

    cx_project.update_remote_project()
    scan_id = cx_project.start_scan()
    clogger.info(f'created scan with id {scan_id}')

    return scan_id


@functools.lru_cache
def component_logger(component_name):
    return logging.getLogger(component_name)

@functools.lru_cache()
def create_checkmarx_client(checkmarx_cfg_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    return checkmarx.client.CheckmarxClient(cfg_fac.checkmarx(checkmarx_cfg_name))
