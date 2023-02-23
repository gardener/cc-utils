import typing

import tabulate

import clamav.model
import github.compliance.model


def as_table(
    scan_results: typing.Iterable[clamav.model.ClamAVResourceScanResult],
    tablefmt: str='simple', # see tabulate module
):
    headers = ('resource', 'status', 'details')

    def row_from_result(scan_result: clamav.model.ClamAVResourceScanResult):
        c = scan_result.scanned_element.component
        a = github.compliance.model.artifact_from_node(scan_result.scanned_element)
        resource = f'{c.name}:{c.version}/{a.name}:{a.version}'
        res = scan_result.scan_result

        status = res.malware_status

        if status is clamav.model.MalwareStatus.OK:
            details = 'no malware found'
        elif status is clamav.model.MalwareStatus.UNKNOWN:
            details = 'failed to scan'
        elif status is clamav.model.MalwareStatus.FOUND_MALWARE:
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
