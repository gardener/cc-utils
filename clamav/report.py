import typing

import tabulate

import clamav.cnudie
import clamav.client_asgi as cac


def as_table(
    scan_results: typing.Iterable[clamav.cnudie.ResourceScanResult],
    tablefmt: str='simple', # see tabulate module
):
    headers = ('resource', 'status', 'details')

    def row_from_result(scan_result: clamav.cnudie.ResourceScanResult):
        resource = f'{scan_result.component.name}/{scan_result.resource.name}'
        res = scan_result.scan_result

        status = res.malware_status

        if status is cac.MalwareStatus.OK:
            details = 'no malware found'
        elif status is cac.MalwareStatus.UNKNOWN:
            details = 'failed to scan'
        elif status is cac.MalwareStatus.FOUND_MALWARE:
            details = '\n'.join((
                f'{finding.name}: {finding.details}' for finding in res.findings
            ))
        else:
            raise NotImplementedError(status)

        return resource, status, details

    def rows():
        for result in scan_results:
            yield row_from_result(scan_result=result)

    return tabulate.tabulate(
        tabular_data=rows(),
        headers=headers,
        tablefmt=tablefmt,
    )
