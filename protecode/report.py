import typing

import protecode.model as pm


def _components_with_greatest_cves(
    result: pm.AnalysisResult
) -> typing.Generator[tuple[pm.Component, list[pm.Vulnerability]], None, None]:
    '''
    determines the greatest cve for given result and returns a generator yielding two-tuples of
    each component and corresponding (unassessed) vulnerabilities that match determined worst CVE
    score.

    if given result does not contain any findings, the iterator will yield nothing
    '''
    worst_cve = result.greatest_cve_score()

    if worst_cve <= 0:
        return

    for component in result.components():
        if component.greatest_cve_score() < worst_cve:
            continue

        vulnerabilities = [
            v for v in component.vulnerabilities()
            if not v.historical() and not v.has_triage() and
                v.cve_severity() >= worst_cve
        ]

        yield component, vulnerabilities


def _component_and_results_to_report_str(
    component: pm.Component,
    vulnerabilities: list[pm.Vulnerability],
) -> str:
    comp = f'{component.name()}:{component.version()}'
    vulns = ', '.join((
        f'{v.cve()} ({v.cve_severity()})' for v in vulnerabilities
    ))

    report = f'{comp} - {vulns}'

    return report


def analysis_result_to_report_str(result: pm.AnalysisResult) -> str:
    return '\n'.join((
        _component_and_results_to_report_str(comp, results)
        for comp, results in _components_with_greatest_cves(result=result)
    ))
