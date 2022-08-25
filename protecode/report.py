import typing

import protecode.model as pm


def _components_with_cves(
    result: pm.AnalysisResult
) -> typing.Generator[tuple[pm.Component, list[pm.Vulnerability]], None, None]:
    '''
    yields two-tuples of components and unassessed, relevant CVEs
    '''
    for component in result.components():
        vulnerabilities = [
            v for v in component.vulnerabilities()
            if not v.historical() and not v.has_triage()
        ]

        if not vulnerabilities:
            continue

        yield component, sorted(vulnerabilities, key=lambda v: v.cve())


def _component_and_results_to_report_str(
    component: pm.Component,
    vulnerabilities: list[pm.Vulnerability],
) -> str:
    comp = f'{component.name()}:{component.version()}'
    vulns = ', '.join((
        f'{v.cve()} ({v.cve_severity()})' for v in vulnerabilities
    ))

    report = f'`{comp}` - `{vulns}`'

    return report


def analysis_result_to_report_str(result: pm.AnalysisResult) -> str:
    components_and_cves = sorted(
        _components_with_cves(result=result),
        key=lambda comp_and_vulns: f'{comp_and_vulns[0].name()}:{comp_and_vulns[0].version()}',
    )

    return '\n'.join((
        _component_and_results_to_report_str(comp, results)
        for comp, results in components_and_cves
    ))
