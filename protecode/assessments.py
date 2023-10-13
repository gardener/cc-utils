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
    '''
    product_id = tgt.product_id()

    assessments_by_c_and_v = collections.defaultdict(list)
    for c, v, triages in assessments:
        assessments_by_c_and_v[f'{c.name()}:{c.version()}:{v.cve()}'] = (c, v, triages)

    for tgt_c in tgt.components():
        for tgt_v in tgt_c.vulnerabilities():
            if tgt_v.historical():
                continue
            if tgt_v.has_triage():
                continue
            if not f'{tgt_c.name()}:{tgt_c.version()}:{tgt_v.cve()}' in assessments_by_c_and_v:
                continue

            c, v, triages = assessments_by_c_and_v[f'{tgt_c.name()}:{tgt_c.version()}:{tgt_v.cve()}']

            if tgt_v.cve() != v.cve():
                continue

            for triage in triages:
                try:
                    protecode_client.add_triage(
                        triage=triage,
                        product_id=product_id,
                        group_id=tgt_group_id,
                    )
                except requests.exceptions.HTTPError as e:
                    # we will re-try importing every scan, so just print a warning
                    logger.warning(
                        f'An error occurred importing {triage=} to {triage.component_name()=} '
                        f'in version {triage.component_version()} for scan {product_id} '
                        f'{e}'
                    )


def auto_triage(
    protecode_client: protecode.client.ProtecodeApi,
    analysis_result: pm.AnalysisResult=None,
    product_id: int=None,
    assessment_txt: str=None,
):
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
