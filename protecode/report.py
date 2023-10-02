import collections
import textwrap
import typing

import dso.labels
import dso.cvss
import protecode.model as pm


def _grouped_component_to_report_table_row(
    comp_name: str,
    cve_and_versions: dict,
    rescoring_rules: typing.Iterable[dso.cvss.RescoringRule] | None=None,
    cve_categorisation: dso.cvss.CveCategorisation | None=None,
) -> str:
    def vuln_str(vuln: pm.Vulnerability):
        if not rescoring_rules or not cve_categorisation or not vuln.cvss:
            rescore = False
        else:
            orig_sev = dso.cvss.CVESeverity.from_cve_score(vuln.cve_severity())

            rules = tuple(dso.cvss.matching_rescore_rules(
                rescoring_rules=rescoring_rules,
                categorisation=cve_categorisation,
                cvss=vuln.cvss,
            ))

            rescored = dso.cvss.rescore(
                rescoring_rules=rules,
                severity=orig_sev,
            )

            if orig_sev is rescored:
                rescore = False
            else:
                rescore = True

        if not rescore:
            return f'`{vuln.cve()}` | `{vuln.cve_severity()}` |'

        return f'`{vuln.cve()}` | `{vuln.cve_severity()}` | `{rescored.name}`'

    vulnerability: pm.Vulnerability = cve_and_versions['vulnerability']
    versions = ', <br/>'.join((f'`{version}`' for version in sorted(
        cve_and_versions['versions'],
        key=lambda version: [x for x in version.split('.')] if version else [f'{version}'],
    )))
    return f'| `{comp_name}` | {vuln_str(vulnerability)} | {versions} |'


def _group_by_component_name_and_cve(
    results: tuple[pm.VulnerabilityScanResult],
) -> dict[str, list[dict]]:
    grouped_components = collections.defaultdict(list)

    for result in results:
        comp_name = result.affected_package.name()
        comp_version = result.affected_package.version()
        vulnerability = result.vulnerability

        for grouped_vulnerability in grouped_components[comp_name]:
            if grouped_vulnerability['vulnerability'].cve() == vulnerability.cve():
                grouped_vulnerability['versions'].append(comp_version)
                break
        else:
            grouped_components[comp_name].append({
                'vulnerability': vulnerability,
                'versions': [comp_version],
            })

    return grouped_components


def scan_result_group_to_report_str(
    results: tuple[pm.VulnerabilityScanResult],
    rescoring_rules: typing.Iterable[dso.cvss.RescoringRule] | None=None,
) -> str:
    scanned_element = results[0].scanned_element

    rescore_label = scanned_element.resource.find_label(
        name=dso.labels.CveCategorisationLabel.name,
    )
    if not rescore_label:
        rescore_label = scanned_element.component.find_label(
            name=dso.labels.CveCategorisationLabel.name,
        )

    if rescore_label:
        rescore_label = dso.labels.deserialise_label(label=rescore_label)
        rescore_label: dso.labels.CveCategorisationLabel
        cve_categorisation = rescore_label.value
    else:
        cve_categorisation = None

    report = '## Summary of found vulnerabilities'
    if cve_categorisation and rescoring_rules:
        report += '\nHint: Rescorings are informative - assessments still need to be done'

    grouped_components = _group_by_component_name_and_cve(results=results)

    report += textwrap.dedent('''
        | Affected Package | CVE | CVE Score | Possible Rescoring | Package Version(s) |
        | ---------------- | :-: | :-------: | :----------------: | ------------------ |
    ''') + '\n'.join(
        _grouped_component_to_report_table_row(
            comp_name=comp_name,
            cve_and_versions=cve_and_versions,
            rescoring_rules=rescoring_rules,
            cve_categorisation=cve_categorisation,
        )
        for comp_name, cves_and_versions in sorted(
            grouped_components.items(),
            key=lambda component: component[0],
        )
        for cve_and_versions in sorted(
            cves_and_versions,
            key=lambda cav: (-cav['vulnerability'].cve_severity(), cav['vulnerability'].cve()),
        )
    )

    return report
