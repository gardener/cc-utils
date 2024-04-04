import collections
import collections.abc
import logging

import requests.exceptions

import dso.labels
import protecode.client
import protecode.model as pm

logger = logging.getLogger(__name__)


def upload_version_hints(
    scan_result: pm.AnalysisResult,
    hints: collections.abc.Iterable[dso.labels.PackageVersionHint],
    client: protecode.client.ProtecodeApi,
) -> pm.AnalysisResult:
    components: tuple[pm.Component] = tuple(scan_result.components())
    product_id = scan_result.product_id()

    for component in components:
        name = component.name()
        version = component.version()

        if version and version != 'unknown':
            # check if package is unique -> in that case we can overwrite the detected version
            if len([c for c in components if c.name() == name]) > 1:
                # not unique, so we cannot overwrite package version
                continue

        for hint in hints:
            if hint.name == name and hint.version != version:
                break
        else:
            continue

        digests = [eo.sha1() for eo in component.extended_objects()]

        client.set_component_version(
            component_name=name,
            component_version=hint.version,
            objects=digests,
            app_id=product_id,
        )

        # the bdba api does not properly react to multiple component versions being set in
        # a short period of time. This even stays true if all component versions are set
        # using one single api request. That's why, adding a small delay in case multiple
        # hints and thus possible version overrides exist by retrieving scan result again
        scan_result = client.wait_for_scan_result(
            product_id=product_id,
            polling_interval_seconds=15, # re-scanning usually don't take a minute
        )

    return scan_result


def add_assessments_if_none_exist(
    tgt: pm.AnalysisResult,
    tgt_group_id: int,
    assessments: collections.abc.Iterable[tuple[pm.Component, pm.Vulnerability, tuple[pm.Triage]]],
    protecode_client: protecode.client.ProtecodeApi,
    assessed_vulns_by_component: dict[str, list[str]]=collections.defaultdict(list),
) -> dict[str, list[str]]:
    '''
    add assessments to given protecode "app"; skip given assessments that are not relevant for
    target "app" (either because there are already assessments, or vulnerabilities do not exit).
    Assessments are added "optimistically", ignoring version differences between source and target
    component versions (assumption: assessments are valid for all component-versions).
    '''
    product_id = tgt.product_id()

    tgt_components_by_name = collections.defaultdict(list)
    for c in tgt.components():
        if not c.version():
            continue # triages require component versions to be set
        tgt_components_by_name[c.name()].append(c)

    for component, vulnerability, triages in assessments:
        if not component.name() in tgt_components_by_name:
            continue

        for tgt_component in tgt_components_by_name[component.name()]:
            for tgt_vulnerability in tgt_component.vulnerabilities():
                if tgt_vulnerability.cve() != vulnerability.cve():
                    continue
                if tgt_vulnerability.historical():
                    continue
                if tgt_vulnerability.has_triage():
                    continue
                # vulnerability is still "relevant" (not obsolete and unassessed)
                break
            else:
                # vulnerability is not longer "relevant" -> skip
                continue

            tgt_component_id = f'{tgt_component.name()}:{tgt_component.version()}'
            if vulnerability.cve() in assessed_vulns_by_component[tgt_component_id]:
                continue

            for triage in triages:
                try:
                    protecode_client.add_triage(
                        triage=triage,
                        product_id=product_id,
                        group_id=tgt_group_id,
                        component_version=tgt_component.version(),
                    )
                    assessed_vulns_by_component[tgt_component_id].append(vulnerability.cve())
                except requests.exceptions.HTTPError as e:
                    # we will re-try importing every scan, so just print a warning
                    logger.warning(
                        f'An error occurred importing {triage=} to {component.name()=} '
                        f'in version {component.version()} for scan {product_id} '
                        f'{e}'
                    )
    return assessed_vulns_by_component


def auto_triage(
    protecode_client: protecode.client.ProtecodeApi,
    analysis_result: pm.AnalysisResult=None,
    product_id: int=None,
    assessment_txt: str=None,
    assessed_vulns_by_component: dict[str, list[str]]=collections.defaultdict(list),
) -> dict[str, list[str]]:
    '''Automatically triage all current vulnerabilities below the given CVSS-threshold on the given
    Protecode scan.

    Components with matching vulnerabilities will be assigned an arbitrary version
    (`[ci]-auto-triage`) since a version is required by Protecode to be able to triage.
    '''
    if not ((product_id is not None) ^ (analysis_result is not None)):
        raise ValueError('exactly one of product_id, analysis_result must be passed')

    if analysis_result:
        product_id = analysis_result.product_id()

    if product_id:
        analysis_result = protecode_client.scan_result(product_id=product_id)

    product_name = analysis_result.name()
    assessment_txt = assessment_txt or 'Auto-generated due to skip-scan label'

    for component in analysis_result.components():
        component_version = component.version()
        for vulnerability in component.vulnerabilities():
            if vulnerability.historical():
                continue
            if vulnerability.has_triage():
                continue

            # component version needs to be set to triage. If we actually have a vulnerability
            # we want to auto-triage we need to set the version first.
            component_name = component.name()
            vulnerability_cve = vulnerability.cve()

            component_id = f'{component_name}:{component_version}'
            if vulnerability_cve in assessed_vulns_by_component[component_id]:
                continue

            if not component_version:
                component_version = '[ci]-auto-triage'
                protecode_client.set_component_version(
                    component_name=component_name,
                    component_version=component_version,
                    scope=pm.VersionOverrideScope.APP,
                    objects=list(o.sha1() for o in component.extended_objects()),
                    app_id=product_id,
                )

            triage_dict = {
                'component': component_name,
                'version': component_version,
                'vulns': [vulnerability_cve],
                'scope': pm.TriageScope.RESULT.value,
                'reason': 'OT', # "other"
                'description': assessment_txt,
                'product_id': product_id,
            }
            logger.debug(
                f'Auto-triaging {vulnerability_cve=} {component_name=} {product_id=} {product_name=}'
            )
            protecode_client.add_triage_raw(
                triage_dict=triage_dict,
            )
            assessed_vulns_by_component[component_id].append(vulnerability_cve)
    return assessed_vulns_by_component
