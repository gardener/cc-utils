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
import enum
import json
import logging
import tempfile
import typing

import tabulate

import concourse.model.traits.image_scan as image_scan
import github.compliance.model as gcm
import github.compliance.report as gcrep
import github.compliance.issue as gciss
import saf.model
import protecode.model as pm
import protecode.report as pr

logger = logging.getLogger()

# monkeypatch: disable html escaping
tabulate.htmlescape = lambda x: x


def scan_result_group_collection_for_vulnerabilities(
    results: tuple[pm.BDBA_ScanResult],
    cve_threshold: float,
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
        return pr.analysis_result_to_report_str(result.result)

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
            return gcm.Severity.CRITICAL

        return None

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=gciss._label_licenses,
        classification_callback=classification_callback,
        findings_callback=has_prohibited_licenses,
    )


def print_protecode_info_table(
    protecode_group_url: str,
    protecode_group_id: int,
    reference_protecode_group_ids: typing.List[int],
    cvss_version: pm.CVSSVersion,
    include_image_references: typing.List[str],
    exclude_image_references: typing.List[str],
    include_image_names: typing.List[str],
    exclude_image_names: typing.List[str],
    include_component_names: typing.List[str],
    exclude_component_names: typing.List[str],
):
    headers = ('Protecode Scan Configuration', '')
    entries = (
        ('Protecode target group id', str(protecode_group_id)),
        ('Protecode group URL', protecode_group_url),
        ('Protecode reference group IDs', reference_protecode_group_ids),
        ('Used CVSS version', cvss_version.value),
        ('Image reference filter (include)', include_image_references),
        ('Image reference filter (exclude)', exclude_image_references),
        ('Image name filter (include)', include_image_names),
        ('Image name filter (exclude)', exclude_image_names),
        ('Component name filter (include)', include_component_names),
        ('Component name filter (exclude)', exclude_component_names),
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


def dump_malware_scan_request(request: saf.model.EvidenceRequest):
    request_dict = dataclasses.asdict(request)
    with tempfile.NamedTemporaryFile(delete=False, mode='wt') as tmp_file:
        tmp_file.write(json.dumps(request_dict, cls=EnumJSONEncoder))
