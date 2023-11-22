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

import logging

import clamav.model
import concourse.model.traits.image_scan as image_scan
import github.compliance.issue as gciss
import github.compliance.model as gcm


logger = logging.getLogger()


def scan_result_group_collection_for_malware(
    results: tuple[clamav.model.ClamAVResourceScanResult],
    rescoring_entries: tuple[image_scan.ClamAVRescoringEntry],
):
    def malware_found(result: clamav.model.ClamAVResourceScanResult):
        if not result.scan_succeeded:
            return False

        if result.scan_result.malware_status is clamav.model.MalwareStatus.FOUND_MALWARE:
            return True
        else:
            return False

    def rescore(scan_result: clamav.model.ScanResult, default: gcm.Severity):
        for entry in rescoring_entries:
            if not entry.digest == scan_result.meta.scanned_content_digest:
                continue
            if not entry.malware_name.lower() in scan_result.details.lower():
                continue

            logger.info(f'rescoring {scan_result=}, according to {entry=} to {entry.severity}')
            return entry.severity

        return default

    def classification_callback(result: clamav.model.ClamAVResourceScanResult):
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

    def findings_callback(result: clamav.model.ClamAVResourceScanResult):
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
