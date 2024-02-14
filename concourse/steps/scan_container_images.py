# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
