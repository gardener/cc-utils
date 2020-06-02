import concurrent.futures
import functools
import traceback
import typing

import ci.util
import checkmarx.project
import checkmarx.util
import mailutil
import product.model
import threading


scans_above_threshold_const = 'scans_above_threshold'
scans_below_threshold_const = 'scans_below_threshold'
failed_components_const = 'failed_components'


def _scan_sources(
        checkmarx_cfg_name: str,
        team_id: str,
        component_descriptor: str,
        threshold: int,
        max_workers: int = 8,
):
    component_descriptor = product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor)
    )

    client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    scan_func = functools.partial(
            checkmarx.project.upload_and_scan_repo,
            checkmarx_client=client,
            team_id=team_id,
    )

    failed_sentinel = object()

    success_count = 0
    failed_count = 0
    components_count = len(tuple(component_descriptor.components()))
    failed_components = []
    lock = threading.Lock()

    def try_scanning(component):
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
            failed_components.append(component)
            return failed_sentinel

    ci.util.info(f'will scan {components_count} component(s)')

    scan_results_above_threshold = []
    scan_results_below_threshold = []

    for scan_result in executor.map(try_scanning, component_descriptor.components()):
        if scan_result is not failed_sentinel:
            if scan_result.scan_result.scanRiskSeverity > threshold:
                scan_results_above_threshold.append(scan_result)
            else:
                scan_results_below_threshold.append(scan_result)

    return {
        scans_above_threshold_const: scan_results_above_threshold,
        scans_below_threshold_const: scan_results_below_threshold,
        failed_components_const: failed_components,
    }


def _send_mail(
    scans: typing.Dict,
    threshold: int,
    email_recipients,
):
    body = checkmarx.util.assemble_mail_body(
        scans_above_threshold=scans.get(scans_above_threshold_const),
        scans_below_threshold=scans.get(scans_below_threshold_const),
        failed_components=scans.get(failed_components_const),
        threshold=threshold,
    )
    try:
        # get standard cfg set for email cfg
        default_cfg_set_name = ci.util.current_config_set_name()
        cfg_factory = ci.util.ctx().cfg_factory()
        cfg_set = cfg_factory.cfg_set(default_cfg_set_name)

        # send mail
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


def _print_scans(
    scans: typing.Dict
):
    # XXX raise if an error occurred?
    if scans.get(scans_above_threshold_const):
        print('\n')
        ci.util.info('Critical scans above threshold')
        checkmarx.util.print_scan_result(scan_results=scans.get(scans_above_threshold_const))
    else:
        ci.util.info('no critical components above threshold found')

    if scans.get(scans_below_threshold_const):
        print('\n')
        ci.util.info('Clean scans below threshold')
        checkmarx.util.print_scan_result(scan_results=scans.get(scans_below_threshold_const))
    else:
        ci.util.info('no scans below threshold to print')

    if scans.get(failed_components_const):
        print('\n')
        failed_components_str = "\n".join(
            (
                component.name() for component in scans.get(failed_components_const)
            )
        )
        ci.util.info(f'failed components:\n{failed_components_str}')


def scan_sources_and_notify(
    checkmarx_cfg_name: str,
    team_id: str,
    component_descriptor: str,
    email_recipients,
    threshold: int = 40,
):
    scans = _scan_sources(
        checkmarx_cfg_name=checkmarx_cfg_name,
        team_id=team_id,
        component_descriptor=component_descriptor,
        threshold=threshold,
    )

    _print_scans(scans=scans)
    if scans.get(scans_above_threshold_const):
        _send_mail(
            scans=scans,
            threshold=threshold,
            email_recipients=email_recipients,
            )
    else:
        ci.util.info('no scans above threshold. Therefore no mail send')
    # codeowner recipient:
    # only send mail if over threshold
