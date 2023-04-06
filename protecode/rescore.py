import typing

import cnudie.iter
import dso.cvss
import protecode.client
import protecode.model


def cve_categorisation(
    resource_node: cnudie.iter.ResourceNode,
    absent_ok: bool=True,
) -> dso.cvss.CveCategorisation | None:
    label_name = dso.labels.CveCategorisationLabel.name
    if label := resource_node.resource.find_label(name=label_name):
        return label.value

    # fallback to component
    return resource_node.component.find_label(name=label_name)


def rescore(
    bdba_client: protecode.client.ProtecodeApi,
    scan_result: protecode.model.AnalysisResult,
    resource_node: cnudie.iter.ResourceNode,
    rescoring_rules: typing.Sequence[dso.cvss.RescoringRule],
    max_rescore_severity: dso.cvss.CVESeverity=dso.cvss.CVESeverity.MEDIUM,
) -> protecode.model.AnalysisResult:
    '''
    rescores bdba-findings for the given resource-node. Rescoring is only possible if
    cve-categorisations are available from categoristion-label in either resource or component.
    '''
    if not (categorisation := cve_categorisation(resource_node=resource_node)):
        return scan_result

    product_id = scan_result.product_id()

    all_components = tuple(scan_result.components())
    components_with_vulnerabilities = [c for c in all_components if tuple(c.vulnerabilities())]

    components_with_vulnerabilities = sorted(
        components_with_vulnerabilities,
        key=lambda c: c.name()
    )

    total_vulns = 0
    total_rescored = 0

    for c in components_with_vulnerabilities:
        if not c.version():
            continue # do not inject dummy-versions in fully automated mode, yet

        vulns_count = 0
        rescored_count = 0
        vulns_to_assess = []

        for v in c.vulnerabilities():
            if v.historical():
                continue
            if v.has_triage():
                continue

            if not v.cvss:
                continue # happens if only cvss-v2 is available - ignore for now

            orig_severity = dso.cvss.CVESeverity.from_cve_score(v.cve_severity())
            if orig_severity > max_rescore_severity:
                continue

            vulns_count += 1

            matching_rules = dso.cvss.matching_rescore_rules(
                rescoring_rules=rescoring_rules,
                categorisation=categorisation,
                cvss=v.cvss,
            )
            rescored = dso.cvss.rescore(
                rescoring_rules=tuple(matching_rules),
                severity=orig_severity,
            )

            if orig_severity is not rescored:
                rescored_count += 1

                if rescored is dso.cvss.CVESeverity.NONE:
                    vulns_to_assess.append(v)

        if vulns_to_assess:
            bdba_client.add_triage_raw({
                'component': c.name(),
                'version': c.version() or 'does-not-matter',
                'vulns': [v.cve() for v in vulns_to_assess],
                'scope': protecode.model.TriageScope.RESULT.value,
                'reason': 'OT',
                'description': 'auto-assessed as irrelevant based on cve-categorisation',
                'product_id': product_id,
            })
            print(f'auto-assessed {len(vulns_to_assess)=}')

        total_vulns += vulns_count
        total_rescored += rescored_count

    print(f'{total_vulns=}, {total_rescored=}')
    if total_rescored > 0:
        print(f'{total_rescored=} - retrieving result again from bdba (this may take a while)')
        scan_result = bdba_client.scan_result(product_id=scan_result.product_id())

    return scan_result
