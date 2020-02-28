import functools
import ci.util
import checkmarx.client
import checkmarx.model

import tabulate


@functools.lru_cache()
def create_checkmarx_client(checkmarx_cfg_name: str):
    cfg_fac = ci.util.ctx().cfg_factory()
    return checkmarx.client.CheckmarxClient(cfg_fac.checkmarx(checkmarx_cfg_name))


def print_scan_result(scan_result: checkmarx.model.ScanResult, tablefmt: str = 'simple'):
    scan_info_header = ('ScanId', 'ComponentName', 'ScanState', 'Start', 'End')
    scan_info_data = (
        (
            scan_result.scan_result.id,
            scan_result.component.name(),
            scan_result.scan_result.status.name,
            scan_result.scan_result.dateAndTime.startedOn,
            scan_result.scan_result.dateAndTime.finishedOn,
         ),
    )

    scan_statistics_header = ('Overall risk severity', 'high', 'medium', 'low', 'info')
    scan_statistics_data = (
        (
            scan_result.scan_result.scanRiskSeverity,
            scan_result.scan_statistic.highSeverity,
            scan_result.scan_statistic.mediumSeverity,
            scan_result.scan_statistic.lowSeverity,
            scan_result.scan_statistic.infoSeverity,
        ),
    )

    scan_info = tabulate.tabulate(
        headers=scan_info_header,
        tabular_data=scan_info_data,
        tablefmt=tablefmt,
    )

    scan_statistics = tabulate.tabulate(
        headers=scan_statistics_header,
        tabular_data=scan_statistics_data,
        tablefmt=tablefmt,
    )

    print(scan_info)

    print(scan_statistics)
