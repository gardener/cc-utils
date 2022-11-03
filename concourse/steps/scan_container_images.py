# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import dataclasses
import datetime
import enum
import json
import logging
import tempfile
import typing

import tabulate

import clamav.client
import clamav.cnudie
import clamav.scan
import concourse.model.traits.image_scan as image_scan
import dso.cvss
import github.compliance.issue as gciss
import github.compliance.model as gcm
import github.compliance.report as gcrep
import protecode.model as pm
import protecode.report as pr
import saf.model

logger = logging.getLogger()

# monkeypatch: disable html escaping
tabulate.htmlescape = lambda x: x


def scan_result_group_collection_for_vulnerabilities(
    results: tuple[pm.BDBA_ScanResult],
    cve_threshold: float,
    rescoring_rules: tuple[dso.cvss.RescoringRule]=None,
):
    def classification_callback(result: pm.BDBA_ScanResult):
        if not (cve_score := result.greatest_cve_score):
            return None

        return gcrep._criticality_classification(cve_score=cve_score)

    def findings_callback(result: pm.BDBA_ScanResult):
        if not (cve_score := result.greatest_cve_score):
            return False
        return cve_score >= cve_threshold

    def comment_callback(result: pm.BDBA_ScanResult):
        rescore_label = result.artifact.find_label(name=dso.labels.CveCategorisationLabel.name)
        if not rescore_label:
            rescore_label = result.component.find_label(name=dso.labels.CveCategorisationLabel.name)

        if rescore_label:
            rescore_label = dso.labels.deserialise_label(label=rescore_label)
            rescore_label: dso.labels.CveCategorisationLabel
            cve_categoriation = rescore_label.value
        else:
            cve_categoriation = None

        return pr.analysis_result_to_report_str(
            result.result,
            rescoring_rules=rescoring_rules,
            cve_categorisation=cve_categoriation,
        )

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=gciss._label_bdba,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
        comment_callback=comment_callback,
    )


def scan_result_group_collection_for_licenses(
    results: tuple[pm.BDBA_ScanResult],
    license_cfg: image_scan.LicenseCfg,
):
    def has_prohibited_licenses(result: pm.BDBA_ScanResult):
        nonlocal license_cfg
        if not license_cfg:
            logger.warning('no license-cfg - will not report license-issues')
            return False
        for license in result.licenses:
            if not license_cfg.is_allowed(license.name()):
                return True
        else:
            return False

    def classification_callback(result: pm.BDBA_ScanResult):
        if has_prohibited_licenses(result=result):
            return gcm.Severity.BLOCKER

        return None

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=gciss._label_licenses,
        classification_callback=classification_callback,
        findings_callback=has_prohibited_licenses,
    )


def scan_result_group_collection_for_malware(
    results: tuple[clamav.scan.ClamAV_ResourceScanResult],
    rescoring_entries: tuple[image_scan.ClamAVRescoringEntry],
):
    def malware_found(result: clamav.scan.ClamAV_ResourceScanResult):
        if not result.scan_succeeded:
            return False

        if result.scan_result.malware_status is clamav.client.MalwareStatus.FOUND_MALWARE:
            return True
        else:
            return False

    def rescore(scan_result: clamav.client.ScanResult, default: gcm.Severity):
        for entry in rescoring_entries:
            if not entry.digest == scan_result.meta.scanned_content_digest:
                continue
            if not entry.malware_name.lower() in scan_result.details.lower():
                continue

            logger.info(f'rescoring {scan_result=}, according to {entry=} to {entry.severity}')
            return entry.severity

        return default

    def classification_callback(result: clamav.scan.ClamAV_ResourceScanResult):
        if not malware_found(result):
            return None

        if not result.scan_result.findings:
            logger.warning(f'{result=} reports malware-found, but has no findings - might be a bug')

        worst_severity = gcm.Severity.NONE
        for finding in result.scan_result.findings:
            worst_severity = max(
                worst_severity,
                rescore(
                    scan_result=finding,
                    default=gcm.Severity.BLOCKER,
                ),
            )

        return worst_severity

    def findings_callback(result: clamav.scan.ClamAV_ResourceScanResult):
        if not malware_found(result=result):
            return False

        severity = classification_callback(result=result)

        if severity is None or severity is gcm.Severity.NONE:
            return False
        else:
            return True

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=gciss._label_malware,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )


def print_protecode_info_table(
    protecode_group_url: str,
    protecode_group_id: int,
    reference_protecode_group_ids: typing.List[int],
    cvss_version: pm.CVSSVersion,
):
    headers = ('Protecode Scan Configuration', '')
    entries = (
        ('Protecode target group id', str(protecode_group_id)),
        ('Protecode group URL', protecode_group_url),
        ('Protecode reference group IDs', reference_protecode_group_ids),
        ('Used CVSS version', cvss_version.value),
    )
    print(tabulate.tabulate(entries, headers=headers))


class EnumJSONEncoder(json.JSONEncoder):
    '''
    a json.JSONEncoder that will encode enum objects using their values
    '''
    def default(self, o):
        if isinstance(o, enum.Enum):
            return o.value
        return super().default(o)


def dump_malware_scan_request(request):
    request_dict = dataclasses.asdict(request)
    with tempfile.NamedTemporaryFile(delete=False, mode='wt') as tmp_file:
        tmp_file.write(json.dumps(request_dict, cls=EnumJSONEncoder))


def prepare_evidence_request(
    scan_results: typing.Iterable[clamav.scan.ClamAV_ResourceScanResult],
    evidence_id: str = 'gardener-mm6',
    pipeline_url: str = None,
) -> clamav.scan.MalwarescanEvidenceRequest:
    '''Prepare an evidence request for the given scan results and return it.

    The returned evidence request contains the _actual_ clamav scans as payload (i.e. the contents
    of the `scan_results` arg without component or resource information), together with meta-
    information for every entry.

    A link between meta-information and scan-results is also created by setting up the `id` attribute
    of the meta-information entry to be the index of the correspondign scan-result.
    '''
    targets = []
    clamav_scan_results = []
    for i, scan_result in enumerate(scan_results):
        clamav_scan_results.append(scan_result.scan_result)
        targets.append(saf.model.ResourceTarget( # noqa
            id=i,
            name=scan_result.artifact.name,
            version=scan_result.artifact.version,
            extra_id=scan_result.artifact.extraIdentity or None,
        ))

    return clamav.scan.MalwarescanEvidenceRequest(
        meta=saf.model.EvidenceMetadata(
            pipeline_url=pipeline_url,
            evidence_id=evidence_id,
            collection_date=datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            targets=targets,
        ),
        EvidenceDataBinary=clamav_scan_results,
    )
