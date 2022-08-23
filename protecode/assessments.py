import collections
import logging
import typing

import requests.exceptions

import dso.labels
import protecode.model as pm
import protecode.client

logger = logging.getLogger(__name__)


def upload_version_hints(
    scan_result: pm.AnalysisResult,
    hints: typing.Iterable[dso.labels.PackageVersionHint],
    client: protecode.client.ProtecodeApi,
):
    for component in scan_result.components():
        version = component.version()
        if version and version != 'unknown':
            continue

        for hint in hints:
            if hint.name == component.name():
                break
        else:
            continue

        digests = [eo.sha1() for eo in component.extended_objects()]

        client.set_component_version(
            component_name=component.name(),
            component_version=hint.version,
            objects=digests,
            app_id=scan_result.product_id(),
        )


def add_assessments_if_none_exist(
    tgt: pm.AnalysisResult,
    tgt_group_id: int,
    assessments: typing.Iterable[tuple[pm.Component, pm.Vulnerability, tuple[pm.Triage]]],
    protecode_client: protecode.client.ProtecodeApi,
):
    '''
    add assessments to given protecode "app"; skip given assessments that are not relevant for
    target "app" (either because there are already assessments, or vulnerabilities do not exit).
    Assessments are added "optimistically", ignoring version differences between source and target
    component versions (assumption: assessments are valid for all component-versions).
    '''
    tgt_components_by_name = collections.defaultdict(list)
    for c in tgt.components():
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

            product_id = tgt.product_id()
            for triage in triages:
                try:
                    protecode_client.add_triage(
                        triage=triage,
                        product_id=product_id,
                        group_id=tgt_group_id,
                        component_version=tgt_component.version(),
                    )
                except requests.exceptions.HTTPError as e:
                    # we will re-try importing every scan, so just print a warning
                    logger.warning(
                        f'An error occurred importing {triage=} to {component.name()=} '
                        f'in version {component.version()} for scan {product_id} '
                        f'{e}'
                    )


def auto_triage(
    analysis_result: pm.AnalysisResult,
    cvss_threshold: float,
    protecode_api: protecode.client.ProtecodeApi,
):
    '''Automatically triage all current vulnerabilities below the given CVSS-threshold on the given
    Protecode scan.

    Components with matching vulnerabilities will be assigned an arbitrary version
    (`[ci]-auto-triage`) since a version is required by Protecode to be able to triage.
    '''
    product_id = analysis_result.product_id()
    product_name = analysis_result.name()

    for component in analysis_result.components():
        component_version = component.version()
        for vulnerability in component.vulnerabilities():
            if (
                vulnerability.cve_severity() >= cvss_threshold and not vulnerability.historical()
                and not vulnerability.has_triage()
            ):
                # component version needs to be set to triage. If we actually have a vulnerability
                # we want to auto-triage we need to set the version first.
                component_name = component.name()
                vulnerability_cve = vulnerability.cve()
                if not component_version:
                    component_version = '[ci]-auto-triage'
                    protecode_api.set_component_version(
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
                    'description': 'Auto-generated due to skip-scan label',
                    'product_id': product_id,
                }
                logger.debug(
                    f'Auto-triaging {vulnerability_cve} for {component_name} '
                    f'in product {product_id} ({product_name})'
                )
                protecode_api.add_triage_raw(
                    triage_dict=triage_dict,
                )
