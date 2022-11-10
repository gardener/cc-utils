import itertools
import logging
import os
import typing

import ccc.protecode
import ccc.oci
import ci.util
import concourse.steps.component_descriptor_util as component_descriptor_util
import concourse.steps.images
import concourse.steps.scan_container_images
import dso.cvss
from protecode.model import CVSSVersion, TriageScope
from protecode.scanning import upload_grouped_images as _upload_grouped_images
import protecode.assessments as pa


__cmd_name__ = 'protecode'
logger = logging.getLogger(__name__)


def rescore(
    protecode_cfg_name: str,
    product_id: int,
    categorisation: str,
    rescoring_rules: str,
    assess: bool=False,
):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)
    client = ccc.protecode.client(protecode_cfg)

    if not os.path.isfile(categorisation):
        print(f'{categorisation} must point to an existing file w/ CveCategorisation')
        exit(1)

    if not os.path.isfile(rescoring_rules):
        print(f'{rescoring_rules} must point to an existing file w/ RescoringRules')
        exit(1)

    categorisation = dso.cvss.CveCategorisation.from_dict(
        ci.util.parse_yaml_file(categorisation),
    )

    rescoring_rules = tuple(
        dso.cvss.rescoring_rules_from_dicts(
            ci.util.parse_yaml_file(rescoring_rules)
        )
    )

    result = client.scan_result(product_id=product_id)

    all_components = tuple(result.components())
    components_with_vulnerabilities = [c for c in all_components if tuple(c.vulnerabilities())]

    print(f'{len(all_components)=}, {len(components_with_vulnerabilities)=}')

    components_with_vulnerabilities = sorted(
        components_with_vulnerabilities,
        key=lambda c: c.name()
    )

    total_vulns = 0
    total_rescored = 0

    for c in components_with_vulnerabilities:
        print(f'{c.name()}:{c.version()}')
        vulns_count = 0
        rescored_count = 0
        vulns_to_assess = []

        for v in c.vulnerabilities():
            if v.historical():
                continue
            if v.has_triage():
                continue

            vulns_count += 1

            if not v.cvss:
                continue # happens if only cvss-v2 is available - ignore for now

            rules = dso.cvss.matching_rescore_rules(
                rescoring_rules=rescoring_rules,
                categorisation=categorisation,
                cvss=v.cvss,
            )
            rules = tuple(rules)
            orig_severity = dso.cvss.CVESeverity.from_cve_score(v.cve_severity())
            rescored = dso.cvss.rescore(
                rescoring_rules=rescoring_rules,
                severity=orig_severity,
            )

            if orig_severity is not rescored:
                rescored_count += 1

                print(f'  rescored {orig_severity.name} -> {rescored.name} - {v.cve()}')
                if assess and rescored is dso.cvss.CVESeverity.NONE:
                    if not c.version():
                        print(f'setting dummy-version for {c.name()}')
                        client.set_component_version(
                            component_name=c.name(),
                            component_version='does-not-matter',
                            objects=[eo.sha1() for eo in c.extended_objects()],
                            app_id=product_id,
                        )
                    else:
                        vulns_to_assess.append(v)

        if assess and vulns_to_assess:
            client.add_triage_raw({
                'component': c.name(),
                'version': c.version() or 'does-not-matter',
                'vulns': [v.cve() for v in vulns_to_assess],
                'scope': TriageScope.RESULT.value,
                'reason': 'OT',
                'description': 'assessed as irrelevant based on cve-categorisation',
                'product_id': product_id,
            })
            print(f'auto-assessed {len(vulns_to_assess)=}')

        print(f'{vulns_count=}, {rescored_count=}')
        total_vulns += vulns_count
        total_rescored += rescored_count

    print()
    print(f'{total_vulns=}, {total_rescored=}')


def assess(
    protecode_cfg_name: str,
    product_id: int,
    assessment: str,
):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)
    client = ccc.protecode.client(protecode_cfg)

    pa.auto_triage(
        protecode_client=client,
        product_id=product_id,
        assessment_txt=assessment,
    )


def scan(
    protecode_cfg_name: str,
    protecode_group_id: str,
    component_descriptor_path: str,
    cve_threshold: float=7.0,
    protecode_api_url=None,
    reference_protecode_group_ids: typing.List[int] = [],
):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    oci_client = ccc.oci.oci_client()

    if not protecode_api_url:
        protecode_api_url = protecode_cfg.api_url()
        logger.info(f'Using Protecode at: {protecode_api_url}')

    protecode_group_url = f'{protecode_api_url}/group/{protecode_group_id}/'
    cd = component_descriptor_util.component_descriptor_from_component_descriptor_path(
        cd_path=component_descriptor_path,
    )

    protecode_api_url = protecode_cfg.api_url()
    protecode_group_url = ci.util.urljoin(protecode_api_url, 'group', str(protecode_group_id))

    cvss_version = CVSSVersion.V3

    concourse.steps.scan_container_images.print_protecode_info_table(
        protecode_group_url=protecode_group_url,
        protecode_group_id=protecode_group_id,
        reference_protecode_group_ids=reference_protecode_group_ids,
        cvss_version=cvss_version,
    )

    logger.info('running protecode scan for all components')

    client = ccc.protecode.client(protecode_cfg_name)

    results = _upload_grouped_images(
        protecode_api=client,
        component=cd,
        protecode_group_id=protecode_group_id,
        oci_client=oci_client,
    )

    results_above_threshold = [r for r in results if r.greatest_cve_score >= cve_threshold]
    results_below_threshold = [r for r in results if r.greatest_cve_score < cve_threshold]

    logger.info('Summary of found vulnerabilities:')
    logger.info(f'{len(results_above_threshold)=}; {results_above_threshold=}')
    logger.info(f'{len(results_below_threshold)=}; {results_below_threshold=}')


def transport_triages(
    protecode_cfg_name: str,
    from_product_id: int,
    to_group_id: int,
    to_product_ids: [int],
):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)
    api = ccc.protecode.client(protecode_cfg)

    scan_result_from = api.scan_result(product_id=from_product_id)
    scan_results_to = {
        product_id: api.scan_result(product_id=product_id)
        for product_id in to_product_ids
    }

    def target_component_versions(product_id: int, component_name: str):
        scan_result = scan_results_to[product_id]
        component_versions = {
            c.version() for c
            in scan_result.components()
            if c.name() == component_name
        }
        return component_versions

    def enum_triages():
        for component in scan_result_from.components():
            for vulnerability in component.vulnerabilities():
                for triage in vulnerability.triages():
                    yield component, triage

    triages = list(enum_triages())
    logger.info(f'found {len(triages)} triage(s) to import')

    for to_product_id, component_name_and_triage in itertools.product(to_product_ids, triages):
        component, triage = component_name_and_triage
        for target_component_version in target_component_versions(
            product_id=to_product_id,
            component_name=component.name(),
        ):
            logger.info(f'adding triage for {triage.component_name()}:{target_component_version}')
            api.add_triage(
                triage=triage,
                product_id=to_product_id,
                group_id=to_group_id,
                component_version=target_component_version,
            )
        logger.info(f'added triage for {triage.component_name()} to {to_product_id}')
