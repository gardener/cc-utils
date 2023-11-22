import itertools
import logging
import os
import pprint

import tabulate

import gci.componentmodel as cm

from protecode.model import CVSSVersion, TriageScope
from protecode.scanning import upload_grouped_images as _upload_grouped_images
import ccc.aws
import ccc.oci
import ccc.protecode
import ci.util
import cnudie.iter
import cnudie.retrieve as cr
import ctx
import dso.cvss
import dso.labels
import oci.model as om
import protecode.assessments as pa
import protecode.model as pm


__cmd_name__ = 'bdba'
logger = logging.getLogger(__name__)

# monkeypatch: disable html escaping
tabulate.htmlescape = lambda x: x


def retrieve(
    product_id: str,
    bdba_cfg_name='gardener',
):
    client = ccc.protecode.client(bdba_cfg_name)

    scan_result = client.wait_for_scan_result(
        product_id=product_id,
    )

    pprint.pprint(scan_result.raw)


def ls_products(
    ocm_component: str,
    bdba_cfg_name='gardener',
    group_id=407,
    ocm_repo: str=None,
):
    if not ocm_repo:
        ocm_lookup = ctx.cfg.ctx.ocm_lookup
    else:
        ocm_lookup = cr.create_default_component_descriptor_lookup(
            ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(
                ocm_repo,
            )
        )

    client = ccc.protecode.client(bdba_cfg_name)

    if not ':' in ocm_component:
        raise ValueError('ocm_component must have form <name>:<version>')

    root_component_descriptor = ocm_lookup(ocm_component)

    for ocm_node in cnudie.iter.iter(
        component=root_component_descriptor,
        lookup=ocm_lookup,
        node_filter=cnudie.iter.Filter.components,
    ):
        component = ocm_node.component

        metadata = {
            'COMPONENT_NAME': component.name,
            'COMPONENT_VERSION': component.version,
        }

        for app in client.list_apps(group_id=group_id, custom_attribs=metadata):
            print(app.product_id())


def rescore(
    protecode_cfg_name: str,
    product_id: int,
    rescoring_rules: str,
    ocm_repo: str=None,
    categorisation: str=None,
    assess: bool=False,
):
    client = ccc.protecode.client(protecode_cfg_name)

    if categorisation and not os.path.isfile(categorisation):
        print(f'{categorisation} must point to an existing file w/ CveCategorisation')
        exit(1)

    if not os.path.isfile(rescoring_rules):
        print(f'{rescoring_rules} must point to an existing file w/ RescoringRules')
        exit(1)

    logger.info(f'retrieving bdba {product_id=}')
    result = client.scan_result(product_id=product_id)

    if categorisation:
        categorisation = dso.cvss.CveCategorisation.from_dict(
            ci.util.parse_yaml_file(categorisation),
        )
    else:
        if not ocm_repo:
            ocm_lookup = ctx.cfg.ctx.ocm_lookup
        else:
            ocm_lookup = cr.create_default_component_descriptor_lookup(
                ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(
                    ocm_repo,
                )
            )

        custom_data = result.custom_data()
        component_name = custom_data['COMPONENT_NAME']
        component_version = custom_data['COMPONENT_VERSION']
        image_name = custom_data['IMAGE_REFERENCE_NAME']
        image_version = custom_data['IMAGE_VERSION']
        logger.info(f'retrieving component descriptor for {component_name}:{component_version}')

        try:
            component_descriptor = ocm_lookup(
                cm.ComponentIdentity(
                    name=component_name,
                    version=component_version,
                ),
            )
        except om.OciImageNotFoundException:
            logger.error(f'could not find {component_name}:{component_version} in {ocm_repo}')
            exit(1)

        logger.info(f'looking for {image_name}:{image_version} in component-descriptor')
        for resource in component_descriptor.component.resources:
            if resource.name != image_name:
                continue
            if resource.version != image_version:
                continue
            break # found it
        else:
            logger.error(
                'did not find {image_name}:{image_version} in {component_name}:{component_version}'
            )
            exit(1)

        label_name = dso.labels.CveCategorisationLabel.name
        categorisation_label = resource.find_label(label_name)
        if not categorisation_label:
            logger.error(f'found image, but it did not have expected {label_name=}')
            logger.error('consider passing categorisation via ARGV')
            exit(1)

        categorisation_label = dso.labels.deserialise_label(categorisation_label)
        categorisation = categorisation_label.value

    rescoring_rules = tuple(
        dso.cvss.rescoring_rules_from_dicts(
            ci.util.parse_yaml_file(rescoring_rules)
        )
    )

    all_components = tuple(result.components())
    components_with_vulnerabilities = [c for c in all_components if tuple(c.vulnerabilities())]

    logger.info(f'{len(all_components)=}, {len(components_with_vulnerabilities)=}')

    components_with_vulnerabilities = sorted(
        components_with_vulnerabilities,
        key=lambda c: c.name()
    )

    total_vulns = 0
    total_rescored = 0

    for c in components_with_vulnerabilities:
        vulns_count = 0
        rescored_count = 0
        vulns_to_assess = []
        printed_cname = False

        for v in c.vulnerabilities():
            if v.historical():
                continue
            if v.has_triage():
                continue

            vulns_count += 1

            if not v.cvss:
                continue # happens if only cvss-v2 is available - ignore for now

            rules = tuple(dso.cvss.matching_rescore_rules(
                rescoring_rules=rescoring_rules,
                categorisation=categorisation,
                cvss=v.cvss,
            ))
            orig_severity = dso.cvss.CVESeverity.from_cve_score(v.cve_severity())
            rescored = dso.cvss.rescore(
                rescoring_rules=rules,
                severity=orig_severity,
            )

            if orig_severity is not rescored:
                rescored_count += 1

                if not printed_cname:
                    print(f'{c.name()}:{c.version()}')
                    printed_cname = True

                print(f'  rescore {orig_severity.name} -> {rescored.name} - {v.cve()}')
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
    client = ccc.protecode.client(protecode_cfg=protecode_cfg)

    pa.auto_triage(
        protecode_client=client,
        product_id=product_id,
        assessment_txt=assessment,
    )


def scan(
    protecode_cfg_name: str,
    protecode_group_id: str,
    component: str,
    cve_threshold: float=7.0,
    protecode_api_url=None,
    reference_protecode_group_ids: list[int]=[],
    aws_cfg: str=None,
):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)

    oci_client = ccc.oci.oci_client()
    if aws_cfg:
        aws_session = ccc.aws.session(aws_cfg=aws_cfg)
        s3_client = aws_session.client('s3')
    else:
        s3_client = None
        logger.warn('failed to initialise s3-client')

    if not protecode_api_url:
        protecode_api_url = protecode_cfg.api_url()
    protecode_group_url = ci.util.urljoin(protecode_api_url, 'group', str(protecode_group_id))
    logger.info(f'Using Protecode at: {protecode_api_url} with group {protecode_group_id}')

    lookup = cnudie.retrieve.create_default_component_descriptor_lookup()
    component_descriptor = lookup(component)

    cvss_version = CVSSVersion.V3

    headers = ('Protecode Scan Configuration', '')
    entries = (
        ('Protecode target group id', str(protecode_group_id)),
        ('Protecode group URL', protecode_group_url),
        ('Protecode reference group IDs', reference_protecode_group_ids),
        ('Used CVSS version', cvss_version.value),
    )
    print(tabulate.tabulate(entries, headers=headers))

    logger.info('running protecode scan for all components')

    client = ccc.protecode.client(
        protecode_cfg=protecode_cfg,
        group_id=protecode_group_id,
        base_url=protecode_api_url,
        cfg_factory=cfg_factory,
    )

    results = tuple(_upload_grouped_images(
        protecode_api=client,
        bdba_cfg_name=protecode_cfg_name,
        component=component_descriptor,
        protecode_group_id=protecode_group_id,
        reference_group_ids=reference_protecode_group_ids,
        oci_client=oci_client,
        s3_client=s3_client,
    ))

    results_above_threshold: list[pm.VulnerabilityScanResult] = [
        r for r in results
        if isinstance(r, pm.VulnerabilityScanResult)
            and r.vulnerability.cve_severity() >= cve_threshold
    ]
    results_below_threshold: list[pm.VulnerabilityScanResult] = [
        r for r in results
        if isinstance(r, pm.VulnerabilityScanResult)
            and r.vulnerability.cve_severity() < cve_threshold
    ]

    logger.info('Summary of found vulnerabilities:')
    logger.info(f'{len(results_above_threshold)=}')
    logger.info(f'{len(results_below_threshold)=}')

    def _grouped_results(results: list[pm.VulnerabilityScanResult]) -> dict:
        grouped_results = dict()
        for r in results:
            c_id = f'{r.scanned_element.component.name}:{r.scanned_element.component.version}'
            a_id = f'{r.scanned_element.resource.name}:{r.scanned_element.resource.version}'
            p_id = f'{r.affected_package.name()}:{r.affected_package.version()}'

            key = f'{c_id}:{a_id}:{p_id}'
            cve = r.vulnerability.cve()
            cve_severity = r.vulnerability.cve_severity()

            if key in grouped_results:
                grouped_results[key]['vulnerabilities'] += f'\n{cve} ({cve_severity})'
            else:
                grouped_results[key] = {
                    'c_id': c_id,
                    'a_id': a_id,
                    'p_id': p_id,
                    'vulnerabilities': f'{cve} ({cve_severity})',
                }
        return grouped_results

    grouped_results_above_threshold = _grouped_results(
        results=results_above_threshold,
    )
    grouped_results_below_threshold = _grouped_results(
        results=results_below_threshold,
    )

    def print_summary(grouped_results: dict):
        print(tabulate.tabulate(
            grouped_results.values(),
            headers={
                'c_id': 'Component ID',
                'a_id': 'Artefact ID',
                'p_id': 'Affected Package ID',
                'vulnerabilities': 'Vulnerabilities',
            },
            tablefmt='grid',
        ))

    print(f'Summary of found vulnerabilites above {cve_threshold=}')
    print_summary(grouped_results=grouped_results_above_threshold)
    print(f'Summary of found vulnerabilites below {cve_threshold=}')
    print_summary(grouped_results=grouped_results_below_threshold)


def transport_triages(
    protecode_cfg_name: str,
    from_product_id: int,
    to_group_id: int,
    to_product_ids: [int],
):
    cfg_factory = ci.util.ctx().cfg_factory()
    protecode_cfg = cfg_factory.protecode(protecode_cfg_name)
    api = ccc.protecode.client(protecode_cfg=protecode_cfg)

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
