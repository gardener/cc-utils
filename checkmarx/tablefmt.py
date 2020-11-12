import tabulate
import textwrap
import typing

import checkmarx.client
import checkmarx.model as model


def get_scan_info_table(
    scan_results: typing.Iterable[model.ScanResult],
    tablefmt: str = 'simple',
):
    scan_info_header = ('Scan ID', 'Artifact Name', 'Scan State', 'Start', 'End')

    def started_on(scan_result: model.ScanResult):
        if scan_result.scan_response:
            return scan_result.scan_response.dateAndTime.startedOn
        else:
            return 'unknown'

    def ended_on(scan_result: model.ScanResult):
        if scan_result.scan_response:
            return scan_result.scan_response.dateAndTime.finishedOn
        else:
            return 'unkown'

    scan_info_data = (
        (
            scan_result.scan_response.id,
            scan_result.artifact_name,
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
        scan_results: typing.Iterable[model.ScanResult],
        routes: checkmarx.client.CheckmarxRoutes,
        tablefmt: str = 'simple',
):
    def artifact_name(scan_result: model.ScanResult):
        if tablefmt == 'html':
            return f'''
            <a href="{routes.web_ui_scan_history(scan_id=scan_result.scan_response.id)}">
                {scan_result.artifact_name}
            </a>
            '''
        else:
            return scan_result.artifact_name

    def scan_severity(scan_result: model.ScanResult):
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
        'Artifact Name',
        'Overall Severity',
        'High',
        'Medium',
        'Low',
        'Info',
    )

    scan_statistics_data = [
        (
            artifact_name(scan_result),
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
        scan_results: typing.Iterable[model.ScanResult],
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
    failed_artifacts: typing.Dict,
    threshold: int,
    routes: checkmarx.client.CheckmarxRoutes,
):
    body_parts = [_mail_disclaimer()]

    if len(scans_above_threshold) > 0:
        above_threshold_text = textwrap.dedent(f'''
            <p>
              The following scan artifacts in checkmarx were found to
              contain critical vulnerabilities (applying threshold {threshold})
            </p>
        ''')
        scan_statistics_above_threshold = get_scan_statistics_tables(
            scan_results=scans_above_threshold,
            tablefmt='html',
            routes=routes,
        )
        body_parts.append(above_threshold_text + scan_statistics_above_threshold)

    if len(scans_below_threshold) > 0:
        below_threshold_text = textwrap.dedent('''
            <p>
              The following scan artifacts were found to be below the configured threshold:
            </p>
        ''')
        scan_statistics_below_threshold = get_scan_statistics_tables(
            scan_results=scans_below_threshold,
            tablefmt='html',
            routes=routes,
        )
        body_parts.append(below_threshold_text + scan_statistics_below_threshold)

    if len(failed_artifacts) > 0:
        failed_artifacts_str = ''.join((
            f'<li>{artifact_name}</li>' for artifact_name in failed_artifacts
        ))
        failed_artifacts_text = textwrap.dedent(
            f'''
                <p>
                  The following artifacts finished in an erroneous state:
                    <ul>{failed_artifacts_str})</ul>
                </p>
            '''
        )
        body_parts.append(failed_artifacts_text)

    return ''.join(body_parts)
