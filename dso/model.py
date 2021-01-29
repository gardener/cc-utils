import dataclasses
import typing

import gci.componentmodel as cm

import dso.labels


# abstraction of component model v2 source and resource
@dataclasses.dataclass
class ScanArtifact:
    name: str
    access: typing.Union[
        cm.OciAccess,
        cm.GithubAccess,
        cm.HttpAccess,
        cm.ResourceAccess,
    ]
    label: dso.labels.ScanLabelValue


@dataclasses.dataclass(frozen=True)
class DependabotCoverageReportRepo:
    repo: str
    dependabot: bool


@dataclasses.dataclass(frozen=False)
class DependabotCoverageReport:
    coverage: float
    github: str
    details: typing.List[DependabotCoverageReportRepo]

    def calculate_overall_percentage(self):
        t = 0
        for rr in self.details:
            if rr.dependabot:
                t += 1

        try:
            self.coverage = t / (len(self.details) / 100)
        except ZeroDivisionError:
            self.coverage = 0
