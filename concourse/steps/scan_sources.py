import concurrent.futures
import functools
import tempfile
import traceback
import typing

import ci.util
import checkmarx.client
import checkmarx.project
import checkmarx.util
import mailutil
import product.model
import threading

import checkmarx.project
import product.util
import whitesource.client
import whitesource.component
from whitesource.component import get_post_project_object
import whitesource.util


scans_above_threshold_const = 'scans_above_threshold'
scans_below_threshold_const = 'scans_below_threshold'
failed_components_const = 'failed_components'


def _scan_sources(
        client: checkmarx.client.CheckmarxClient,
        team_id: str,
        component_descriptor: str,
        threshold: int,
        max_workers: int = 8,
):
    component_descriptor = product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor)
    )

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
            if scan_result.scan_response.scanRiskSeverity > threshold:
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
    routes: checkmarx.client.CheckmarxRoutes,
):
    body = checkmarx.util.assemble_mail_body(
        scans_above_threshold=scans.get(scans_above_threshold_const),
        scans_below_threshold=scans.get(scans_below_threshold_const),
        failed_components=scans.get(failed_components_const),
        threshold=threshold,
        routes=routes,
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
    scans: typing.Dict,
    routes: checkmarx.client.CheckmarxRoutes,
):
    # XXX raise if an error occurred?
    if scans.get(scans_above_threshold_const):
        print('\n')
        ci.util.info('Critical scans above threshold')
        checkmarx.util.print_scan_result(
            scan_results=scans.get(scans_above_threshold_const),
            routes=routes,
        )
    else:
        ci.util.info('no critical components above threshold found')

    if scans.get(scans_below_threshold_const):
        print('\n')
        ci.util.info('Clean scans below threshold')
        checkmarx.util.print_scan_result(
            scan_results=scans.get(scans_below_threshold_const),
            routes=routes,
        )
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
    checkmarx_client = checkmarx.util.create_checkmarx_client(checkmarx_cfg_name)

    scans = _scan_sources(
        client=checkmarx_client,
        team_id=team_id,
        component_descriptor=component_descriptor,
        threshold=threshold,
    )

    _print_scans(
        scans=scans,
        routes=checkmarx_client.routes,
    )

    _send_mail(
        scans=scans,
        threshold=threshold,
        email_recipients=email_recipients,
        routes=checkmarx_client.routes,
    )
    # codeowner recipient:
    # only send mail if over threshold


def scan_component_with_whitesource(whitesource_cfg_name: str,
                                    product_token: str,
                                    component_descriptor_path: str,
                                    extra_whitesource_config: dict,
                                    requester_mail: str):

    # create whitesource client
    ci.util.info('creating whitesource client...')
    client = whitesource.util.create_whitesource_client(whitesource_cfg_name=whitesource_cfg_name)
    ci.util.info('whitesource client created')

    # parse component_descriptor
    ci.util.info('parsing component descriptor...')
    component_descriptor = product.model.ComponentDescriptor.from_dict(
        ci.util.parse_yaml_file(component_descriptor_path)
    )
    ci.util.info('component descriptor parsed\n')

    # for component in []: "components matching component_descriptor"
    for component in component_descriptor.components():

        # create whitesource component
        ci.util.info('preparing POST project for {}...'.format(component.name()))
        post_project_object = get_post_project_object(
            whitesource_client=client,
            product_token=product_token,
            component=component)
        ci.util.info('POST project prepared')

        # store in tmp file
        with tempfile.TemporaryFile() as tmp_file:

            ci.util.info('guessing commit hash...')
            # guess git-ref for the given component's version
            commit_hash = product.util.guess_commit_from_ref(component)
            ci.util.info('commit hash guessed')

            # download whitesource component
            ci.util.info('downloading component for scan...')
            whitesource.component.download_component(
                github_api=post_project_object.github_api,
                component_name=post_project_object.component_name,
                dest=tmp_file,
                ref=commit_hash)
            ci.util.info('component downloaded')

            # POST component>
            ci.util.info('POST project...')
            post_project_object.whitesource_client.post_product(
                product_token=post_project_object.product_token,
                component_name=post_project_object.component_name.name(),
                requester_email=requester_mail,
                extra_whitesource_config=extra_whitesource_config,
                file=tmp_file)
            ci.util.info('project posted\n')
