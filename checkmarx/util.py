import functools
import ci.util
import checkmarx.client
import checkmarx.model

import tabulate
import textwrap
import typing


@functools.lru_cache()
def create_checkmarx_client(checkmarx_cfg_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    return checkmarx.client.CheckmarxClient(cfg_fac.checkmarx(checkmarx_cfg_name))


def is_scan_finished(scan: checkmarx.model.ScanResponse):
    if checkmarx.model.ScanStatusValues(scan.status.id) in (
            checkmarx.model.ScanStatusValues.FINISHED,
            checkmarx.model.ScanStatusValues.FAILED,
            checkmarx.model.ScanStatusValues.CANCELED,
    ):
        return True
    else:
        return False


def is_scan_necessary(project: checkmarx.model.ProjectDetails, hash: str):
    remote_hash = project.get_custom_field(checkmarx.model.CustomFieldKeys.HASH)
    if remote_hash != hash:
        print(f'{remote_hash=} != {hash=} - scan required')
        return True
    else:
        return False


def get_scan_info_table(
        scan_results: typing.Iterable[checkmarx.model.ScanResult],
        tablefmt: str = 'simple',
):
    scan_info_header = ('ScanId', 'ComponentName', 'ScanState', 'Start', 'End')

    def started_on(scan_result):
        return scan_result.scan_response.dateAndTime.startedOn if \
            scan_result.scan_response else 'unknown'

    def ended_on(scan_result):
        return scan_result.scan_response.dateAndTime.finishedOn if \
            scan_result.scan_response else 'unknown'

    scan_info_data = (
        (
            scan_result.scan_response.id,
            scan_result.component.name(),
            scan_result.scan_response.status.name,
            started_on(scan_result),
            ended_on(scan_result),
        ) for scan_result in scan_results
    )
    scan_info = tabulate.tabulate(
        headers=scan_info_header,
        tabular_data=scan_info_data,
        tablefmt=tablefmt,
    )
    return scan_info


def get_scan_statistics_tables(
        scan_results: typing.Iterable[checkmarx.model.ScanResult],
        routes: checkmarx.client.CheckmarxRoutes,
        tablefmt: str = 'simple',
):
    def component_name(scan_result: checkmarx.model.ScanResult):
        if tablefmt == 'html':
            return f'''
            <a href="{routes.web_ui_scan_history(scan_id=scan_result.scan_response.id)}">
                {scan_result.component.name()}
            </a>
            '''
        else:
            return scan_result.component.name()

    def scan_severity(scan_result: checkmarx.model.ScanResult):
        if tablefmt == 'html':
            scan_id = scan_result.scan_response.id
            project_id = scan_result.project_id
            return f'''
            <a href="{routes.web_ui_scan_viewer(scan_id=scan_id, project_id=project_id)}">
                {scan_result.scan_response.scanRiskSeverity}
            </a>
            '''
        else:
            return scan_result.scan_response.scanRiskSeverity

    scan_statistics_header = (
        'ComponentName',
        'Overall severity',
        'high',
        'medium',
        'low',
        'info'
    )

    scan_statistics_data = [
        (
            component_name(scan_result),
            scan_severity(scan_result),
            scan_result.scan_statistic.highSeverity,
            scan_result.scan_statistic.mediumSeverity,
            scan_result.scan_statistic.lowSeverity,
            scan_result.scan_statistic.infoSeverity,
        ) for scan_result in scan_results
    ]

    # monkeypatch: disable html escaping
    tabulate.htmlescape = lambda x: x
    scan_statistics = tabulate.tabulate(
        headers=scan_statistics_header,
        tabular_data=sorted(scan_statistics_data, key=lambda x: x[1], reverse=True),
        tablefmt=tablefmt,
        colalign=('left', 'center', 'center', 'center', 'center', 'center')
    )

    return scan_statistics


def print_scan_result(
        scan_results: typing.Iterable[checkmarx.model.ScanResult],
        routes: checkmarx.client.CheckmarxRoutes,
):
    scan_info_table = get_scan_info_table(scan_results=scan_results, tablefmt='simple')
    scan_statistics_table = get_scan_statistics_tables(
        scan_results=scan_results,
        tablefmt='simple',
        routes=routes,
    )

    print(scan_info_table)
    print('\n')
    print(scan_statistics_table)


def _mail_disclaimer():
    return textwrap.dedent('''
        <div>
          <p>
          Note: you receive this E-Mail, because you were configured as a mail recipient
          (see .ci/pipeline_definitions)
          To remove yourself, search for your e-mail address in said file and remove it.
          </p>
        </div>
    ''')


def assemble_mail_body(
        scans_above_threshold: typing.Dict,
        scans_below_threshold: typing.Dict,
        failed_components: typing.Dict,
        threshold: int,
        routes: checkmarx.client.CheckmarxRoutes,
):
    body_parts = [_mail_disclaimer()]

    if len(scans_above_threshold) > 0:
        above_threshold_text = textwrap.dedent(f'''
            <p>
              The following components in checkmarx were found to
              contain critical vulnerabilities (applying threshold {threshold})
            </p>
        ''')
        scan_statistics_above_threshold = checkmarx.util.get_scan_statistics_tables(
            scan_results=scans_above_threshold,
            tablefmt='html',
            routes=routes,
        )
        body_parts.append(above_threshold_text + scan_statistics_above_threshold)

    if len(scans_below_threshold) > 0:
        below_threshold_text = textwrap.dedent('''
            <p>
              The following components were found to be below the configured threshold:
            </p>
        ''')
        scan_statistics_below_threshold = checkmarx.util.get_scan_statistics_tables(
            scan_results=scans_below_threshold,
            tablefmt='html',
            routes=routes,
        )
        body_parts.append(below_threshold_text + scan_statistics_below_threshold)

    if len(failed_components) > 0:
        failed_components_str = ''.join((
            f'<li>{component.name()}</li>' for component in failed_components
        ))
        failed_components_text = textwrap.dedent(
            f'''
                <p>
                  The following components finished in an erroneous state:
                    <ul>{failed_components_str})</ul>
                </p>
            '''
        )
        body_parts.append(failed_components_text)

    return ''.join(body_parts)
