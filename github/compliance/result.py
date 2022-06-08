import dataclasses
import typing

import gci.componentmodel as cm


@dataclasses.dataclass
class ScanResult:
    component: cm.Component
    resource: cm.Resource


'''
callback type accepting a ScanResult; expected to return True iff argument has a "finding" and False
otherwise.

Definition of "finding" is type-specific
'''
FindingsCallback = typing.Callable[[ScanResult], bool]


@dataclasses.dataclass
class ScanResultGroup:
    '''
    a group of scan results (grouped by component-name and resource-name)
    grouping is done so different component-resource-versions are grouped into common "reporting
    targets" (github issues if used in the context of this package)

    components and resources are understood as defined by the OCM (gci.componentmodel)
    '''
    name: str # {component.name}:{resource.name}
    results: list[ScanResult]

    @property
    def component(self) -> cm.Component:
        return self.results[0].component

    @property
    def resource_name(self) -> cm.Resource:
        return self.results[0].resource

    @property
    def has_findings(self, findings_callback: FindingsCallback) -> bool:
        for r in self.results:
            if findings_callback(r):
                return True
        else:
            return False

    @property
    def results_with_findings(self, findings_callback: FindingsCallback) -> list[ScanResult]:
        return [r for r in self.results if findings_callback(r)]

    @property
    def results_without_findings(self, findings_callback: FindingsCallback) -> list[ScanResult]:
        return [r for r in self.results if not findings_callback(r)]


@dataclasses.dataclass
class ScanResultGroupCollection:
    results: tuple[ScanResult]
    github_issue_label: str

    @property
    def result_groups(self) -> tuple[ScanResultGroup]:
        result_groups = {}

        if not self.results:
            return ()

        for result in self.results:
            group_name = f'{result.component.name}:{result.resource.name}'
            if not group_name in result_groups:
                result_groups[group_name] = ScanResultGroup(
                    name=group_name,
                    results=[result],
                )
            else:
                result_groups[group_name].results.append(result)

        return tuple(result_groups.values())
