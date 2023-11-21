import collections
import logging
import typing

import cnudie.iter
import dso.cvss
import dso.labels
import protecode.client
import protecode.model as pm

logger = logging.getLogger(__name__)


def cve_categorisation(
    resource_node: cnudie.iter.ResourceNode,
) -> dso.cvss.CveCategorisation | None:
    label_name = dso.labels.CveCategorisationLabel.name
    label = resource_node.resource.find_label(name=label_name)
    if not label:
        # fallback to component
        label = resource_node.component.find_label(name=label_name)

    if not label:
        return None

    return dso.labels.deserialise_label(label).value


def rescore(
    bdba_client: protecode.client.ProtecodeApi,
    scan_result: pm.AnalysisResult,
    scanned_element: cnudie.iter.ResourceNode,
    rescoring_rules: typing.Sequence[dso.cvss.RescoringRule],
    max_rescore_severity: dso.cvss.CVESeverity=dso.cvss.CVESeverity.MEDIUM,
    assessed_vulns_by_component: dict[str, list[str]]=collections.defaultdict(list),
) -> dict[str, list[str]]:
    '''
    rescores bdba-findings for the scanned element of the given components scan result.
    Rescoring is only possible if cve-categorisations are available from categoristion-label
    in either resource or component.
    '''
    if not (categorisation := cve_categorisation(resource_node=scanned_element)):
        return assessed_vulns_by_component

    product_id = scan_result.product_id()

    logger.info(f'rescoring {scan_result.display_name()} - {product_id=}')

    all_components = tuple(scan_result.components())
    components_with_vulnerabilities = [c for c in all_components if tuple(c.vulnerabilities())]

    components_with_vulnerabilities = sorted(
        components_with_vulnerabilities,
        key=lambda c: c.name()
    )

    for c in components_with_vulnerabilities:
        if not c.version():
            continue # do not inject dummy-versions in fully automated mode, yet

        vulns_to_assess = []

        for v in c.vulnerabilities():
            if v.historical():
                continue
            if v.has_triage():
                continue

            if not v.cvss:
                continue # happens if only cvss-v2 is available - ignore for now

            component_id = f'{c.name()}:{c.version()}'
            if v.cve() in assessed_vulns_by_component[component_id]:
                continue

            orig_severity = dso.cvss.CVESeverity.from_cve_score(v.cve_severity())
            if orig_severity > max_rescore_severity:
                continue

            matching_rules = dso.cvss.matching_rescore_rules(
                rescoring_rules=rescoring_rules,
                categorisation=categorisation,
                cvss=v.cvss,
            )
            rescored = dso.cvss.rescore(
                rescoring_rules=tuple(matching_rules),
                severity=orig_severity,
            )

            if rescored is dso.cvss.CVESeverity.NONE:
                vulns_to_assess.append(v)
                assessed_vulns_by_component[component_id].append(v.cve())

        if vulns_to_assess:
            logger.info(f'{len(vulns_to_assess)=}: {[v.cve() for v in vulns_to_assess]}')
            bdba_client.add_triage_raw({
                'component': c.name(),
                'version': c.version(),
                'vulns': [v.cve() for v in vulns_to_assess],
                'scope': pm.TriageScope.RESULT.value,
                'reason': 'OT',
                'description': 'auto-assessed as irrelevant based on cve-categorisation',
                'product_id': product_id,
            })

    return assessed_vulns_by_component
