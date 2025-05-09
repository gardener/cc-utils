import collections
import collections.abc
import dataclasses
import enum
import functools
import typing

import cnudie.iter
import ocm


class Severity(enum.IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 4
    CRITICAL = 8
    BLOCKER = 16

    def __str__(self):
        return self.name.lower()

    def __repr__(self):
        return "'" + self.name.lower() + "'"

    @staticmethod
    def parse(value: int | str):
        '''
        convenience method that will behave like default-c'tor, but also accept str-values
        (ignoring type for additional convenience)
        '''
        if isinstance(value, str):
            return Severity[value.upper()]

        return Severity(value)


@dataclasses.dataclass(frozen=True)
class MaxProcessingTimesDays:
    '''
    defines maximum processing time in days, based on issue "criticality"

    used for deserialisation from pipeline-definitions
    '''
    blocker: int = 0
    very_high_or_greater: int = 30
    high: int = 30
    medium: int = 90
    low: int = 120

    def for_severity(self, severity: Severity):
        if severity is Severity.BLOCKER:
            return self.blocker
        elif severity is Severity.CRITICAL:
            return self.very_high_or_greater
        elif severity is Severity.HIGH:
            return self.high
        elif severity is Severity.MEDIUM:
            return self.medium
        elif severity is Severity.LOW:
            return self.low


class ScanState(enum.Enum):
    '''
    indicates the scan outcome of a scan (regardless of yielded contents).

    SUCCEEDED:  scan succeeded without errors (but potentially with findings)
    SKIPPED:    No scan took place (we have an earlier scan that has not been invalidated yet)
    FAILED:     scan failed (which typically implies there are not scan results)
    '''
    SUCCEEDED = 'succeeded'
    SKIPPED = 'skipped'
    FAILED = 'failed'


Target = typing.Union[cnudie.iter.ResourceNode, cnudie.iter.SourceNode, 'cmm.CfgElementStatusReport']


@dataclasses.dataclass(kw_only=True)
class ScanResult:
    scanned_element: Target

    state: ScanState = ScanState.SUCCEEDED

    @property
    def scan_succeeded(self) -> bool:
        return self.state in [ScanState.SUCCEEDED, ScanState.SKIPPED]


@dataclasses.dataclass
class CfgScanResult(ScanResult):
    evaluation_result: 'cmm.CfgStatusEvaluationResult'


FindingsCallback = collections.abc.Callable[[ScanResult], bool]
'''
callback type accepting a ScanResult; expected to return True iff argument has a "finding" and False
otherwise.

Definition of "finding" is type-specific
'''
ClassificationCallback = collections.abc.Callable[[ScanResult], Severity]


def is_ocm_artefact_node(
    element: cnudie.iter.SourceNode | cnudie.iter.ResourceNode | object,
):
    if isinstance(element, (cnudie.iter.SourceNode, cnudie.iter.ResourceNode)):
        return True

    return False


@dataclasses.dataclass
class ScanResultGroup:
    '''
    a group of scan results (grouped by scanned_element + latest processing date if it exists)
    grouping is done so alike scanned_elements are grouped into common "reporting
    targets" (github issues if used in the context of this package)

    components and artifacts are understood as defined by the OCM (ocm)

    ScanResultGroup caches calculated values to reduce amount of (potentially expensive) callbacks.
    Altering `results`, or external state passed-in callbacks rely on will thus result in
    inconsistent state.
    '''
    name: str
    # {component.name}:{artifact.name} |
    # {cfg_element_storage}/{cfg_element_type}/{cfg_element_name}
    results: tuple[ScanResult]
    issue_type: str
    findings_callback: FindingsCallback
    classification_callback: ClassificationCallback

    @property
    def results_with_successful_scans(self):
        return tuple(r for r in self.results if r.scan_succeeded)

    @property
    def component(self) -> ocm.Component:
        result = self.results[0]
        if not is_ocm_artefact_node(result.scanned_element):
            raise RuntimeError('property not allowed to be used if scanned_element is '
                'not either a ResourceNode or a SourceNode')

        return result.scanned_element.component

    @property
    def artifact(self) -> ocm.Source | ocm.Resource:
        result = self.results[0]
        if not is_ocm_artefact_node(result.scanned_element):
            raise RuntimeError('property not allowed to be used if scanned_element is '
                'not either a ResourceNode or a SourceNode')

        return artifact_from_node(result.scanned_element)

    @functools.cached_property
    def has_findings(self) -> bool:
        for r in self.results_with_successful_scans:
            if r.state is ScanState.SKIPPED:
                continue
            if self.findings_callback(r):
                return True
        else:
            return False

    @functools.cached_property
    def has_scan_errors(self) -> bool:
        for result in self.results:
            if not result.scan_succeeded:
                return True

        return False

    @functools.cached_property
    def has_attempted_scans(self) -> bool:
        for result in self.results:
            if not result.state is ScanState.SKIPPED:
                return True
        return False

    @functools.cached_property
    def worst_severity(self) -> Severity:
        if not self.has_findings:
            return None
        classifications = [self.classification_callback(r) for r in self.results_with_findings]
        return max(classifications)

    @functools.cached_property
    def worst_result(self) -> ScanResult:
        if not self.has_findings:
            return None

        worst_severity = self.worst_severity

        for result in self.results_with_findings:
            if self.classification_callback(result) is worst_severity:
                return result

        return None

    @functools.cached_property
    def results_with_findings(self) -> tuple[ScanResult]:
        return tuple(
            r for r in self.results_with_successful_scans
            if r.state is not ScanState.SKIPPED and self.findings_callback(r)
        )

    @functools.cached_property
    def results_without_findings(self) -> tuple[ScanResult]:
        return tuple((
            r for r in self.results_with_successful_scans
            if r.state is not ScanState.SKIPPED and not self.findings_callback(r)
        ))


@dataclasses.dataclass
class ScanResultGroupCollection:
    results: tuple[ScanResult]
    issue_type: str
    classification_callback: ClassificationCallback
    findings_callback: FindingsCallback

    @property
    def result_groups(self) -> tuple[ScanResultGroup]:
        if not self.results:
            return ()

        grouped_results = collections.defaultdict(list)

        for result in self.results:
            if is_ocm_artefact_node(result.scanned_element):
                c = result.scanned_element.component
                a = artifact_from_node(result.scanned_element)
                group_name = f'{c.name}:{a.name}'
            else:
                group_name = result.scanned_element.name

            grouped_results[group_name].append(result)

        return tuple((
            ScanResultGroup(
                name=group_name,
                results=results,
                issue_type=self.issue_type,
                findings_callback=self.findings_callback,
                classification_callback=self.classification_callback,
            ) for group_name, results in grouped_results.items()
        ))

    @property
    def result_groups_with_findings(self) -> tuple[ScanResultGroup]:
        return tuple(
            (rg for rg in self.result_groups if rg.has_findings)
        )

    @property
    def result_groups_without_findings(self) -> tuple[ScanResultGroup]:
        return tuple(
            (
                rg for rg in self.result_groups
                if not rg.has_findings and not rg.has_scan_errors
            )
        )

    @property
    def result_groups_with_scan_errors(self) -> tuple[ScanResultGroup]:
        return tuple(
            (rg for rg in self.result_groups if rg.has_scan_errors)
        )


def artifact_from_node(
    node: cnudie.iter.ResourceNode | cnudie.iter.SourceNode,
) -> ocm.Source | ocm.Resource:
    if isinstance(node, cnudie.iter.SourceNode):
        return node.source
    elif isinstance(node, cnudie.iter.ResourceNode):
        return node.resource
    else:
        raise TypeError(node)
