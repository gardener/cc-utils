
import cfg_mgmt.model as cmm
import github.compliance.issue as gci
import github.compliance.model as gcm


def scan_result_group_collection_for_outdated(results: tuple[gcm.CfgScanResult]) \
    -> gcm.ScanResultGroupCollection:
    issue_type = gci._label_outdated_credentials
    policy_violation = cmm.CfgElementPolicyViolation.CREDENTIALS_OUTDATED

    def classification_callback(result: gcm.CfgScanResult) -> gcm.Severity:
        return gcm.Severity.HIGH

    def findings_callback(result: gcm.CfgScanResult) -> bool:
        policy_violations = result.evaluation_result.nonCompliantReasons

        if policy_violation in policy_violations:
            return True

        return False

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=issue_type,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )


def scan_result_group_collection_for_no_status(results: tuple[gcm.CfgScanResult]) \
    -> gcm.ScanResultGroupCollection:
    issue_type = gci._label_no_status
    policy_violation = cmm.CfgElementPolicyViolation.NO_STATUS

    def classification_callback(result: gcm.CfgScanResult) -> gcm.Severity:
        return gcm.Severity.HIGH

    def findings_callback(result: gcm.CfgScanResult) -> bool:
        policy_violations = result.evaluation_result.nonCompliantReasons

        if policy_violation in policy_violations:
            return True

        return False

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=issue_type,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )


def scan_result_group_collection_for_no_responsible(results: tuple[gcm.CfgScanResult]) \
    -> gcm.ScanResultGroupCollection:
    issue_type = gci._label_no_responsible
    policy_violation = cmm.CfgElementPolicyViolation.NO_RESPONSIBLE

    def classification_callback(result: gcm.CfgScanResult) -> gcm.Severity:
        return gcm.Severity.MEDIUM

    def findings_callback(result: gcm.CfgScanResult) -> bool:
        policy_violations = result.evaluation_result.nonCompliantReasons

        if policy_violation in policy_violations:
            return True

        return False

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=issue_type,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )


def scan_result_group_collection_for_no_rule(results: tuple[gcm.CfgScanResult]) \
    -> gcm.ScanResultGroupCollection:
    issue_type = gci._label_no_rule
    policy_violation = cmm.CfgElementPolicyViolation.NO_RULE

    def classification_callback(result: gcm.CfgScanResult) -> gcm.Severity:
        return gcm.Severity.MEDIUM

    def findings_callback(result: gcm.CfgScanResult) -> bool:
        policy_violations = result.evaluation_result.nonCompliantReasons

        if policy_violation in policy_violations:
            return True

        return False

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=issue_type,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )


def scan_result_group_collection_for_undefined_policy(results: tuple[gcm.CfgScanResult]) \
    -> gcm.ScanResultGroupCollection:
    issue_type = gci._label_undefined_policy
    policy_violation = cmm.CfgElementPolicyViolation.ASSIGNED_RULE_REFERS_TO_UNDEFINED_POLICY

    def classification_callback(result: gcm.CfgScanResult) -> gcm.Severity:
        return gcm.Severity.MEDIUM

    def findings_callback(result: gcm.CfgScanResult) -> bool:
        policy_violations = result.evaluation_result.nonCompliantReasons

        if policy_violation in policy_violations:
            return True

        return False

    return gcm.ScanResultGroupCollection(
        results=tuple(results),
        issue_type=issue_type,
        classification_callback=classification_callback,
        findings_callback=findings_callback,
    )
