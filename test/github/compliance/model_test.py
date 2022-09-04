import dataclasses

import github.compliance.model as gcm


@dataclasses.dataclass
class Component:
    name: str = 'component1'


@dataclasses.dataclass
class Artefact:
    name: str = 'artefact1'


@dataclasses.dataclass
class ScanResult(gcm.ScanResult):
    component = Component()
    artifact = Artefact()
    state = gcm.ScanState.SUCCEEDED
    severity: gcm.Severity = gcm.Severity.HIGH


def test_ScanResultGroup():
    empty_group = gcm.ScanResultGroup(
        name='n',
        results=(),
        issue_type='t',
        findings_callback=None,
        classification_callback=None,
        comment_callback=None,
    )

    assert empty_group.results_with_successful_scans == ()
    assert empty_group.has_findings is False
    assert empty_group.has_scan_errors is False
    assert empty_group.worst_severity is None
    assert empty_group.worst_result is None
    assert empty_group.results_with_findings == ()
    assert empty_group.results_without_findings == ()

    result_medium = ScanResult(
        component=Component(),
        artifact=Artefact(),
        severity=gcm.Severity.MEDIUM,
    )
    result_critical = ScanResult(
        component=Component(),
        artifact=Artefact(),
        severity=gcm.Severity.CRITICAL,
    )

    results = (result_medium, result_critical)

    group_with_findings = gcm.ScanResultGroup(
        name='gwf',
        results=results,
        issue_type='it',
        findings_callback=lambda f: True,
        classification_callback=lambda f: f.severity,
        comment_callback=None,
    )

    assert group_with_findings.results_with_successful_scans == results
    assert group_with_findings.has_findings
    assert group_with_findings.has_scan_errors is False
    assert group_with_findings.worst_severity is gcm.Severity.CRITICAL
    assert group_with_findings.worst_result is result_critical
    assert group_with_findings.results_with_findings == results
    assert group_with_findings.results_without_findings == ()

    result_with_scan_error = ScanResult(
        component=Component(),
        artifact=Artefact(),
        state=gcm.ScanState.FAILED,
    )

    results = (result_critical, result_with_scan_error)

    group_with_scan_errors = gcm.ScanResultGroup(
        name='gwse',
        results=results,
        issue_type='it',
        findings_callback=lambda f: True,
        classification_callback=lambda f: f.severity,
        comment_callback=None,
    )

    assert group_with_scan_errors.results_with_successful_scans == (result_critical,)
    assert group_with_scan_errors.has_findings
    assert group_with_scan_errors.has_scan_errors
    assert group_with_scan_errors.worst_severity is gcm.Severity.CRITICAL
    assert group_with_scan_errors.worst_result is result_critical
    assert group_with_scan_errors.results_with_findings == (result_critical,)
    assert group_with_scan_errors.results_without_findings == ()


def test_ScanResultGroupCollection_result_groups():
    # empty results
    srgc = gcm.ScanResultGroupCollection(
        results=(),
        issue_type='dont/care',
        classification_callback=None,
        findings_callback=None,
    )

    assert srgc.result_groups == ()

    # one group (same component-name/artefact-name)
    results = (
        gcm.ScanResult(
            component=Component(name='c1'),
            artifact=Artefact(name='a1'),
        ),
        gcm.ScanResult(
            component=Component(name='c1'),
            artifact=Artefact(name='a1'),
        ),
    )

    srgc = gcm.ScanResultGroupCollection(
        results=results,
        issue_type='dont/care',
        classification_callback=None,
        findings_callback=None,
    )

    assert len((res_groups := srgc.result_groups)) == 1

    assert tuple(results) == tuple(res_groups[0].results)

    # two groups (different component-name/artefact-name)
    results = (
        gcm.ScanResult(
            component=Component(name='c1'),
            artifact=Artefact(name='a1'),
        ),
        gcm.ScanResult(
            component=Component(name='c2'),
            artifact=Artefact(name='a2'),
        ),
    )

    srgc = gcm.ScanResultGroupCollection(
        results=results,
        issue_type='dont/care',
        classification_callback=None,
        findings_callback=None,
    )
    assert len(srgc.result_groups) == 2
