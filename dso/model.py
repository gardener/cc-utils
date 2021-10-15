import dataclasses
import datetime
from enum import Enum
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
    label: dso.labels.ScanningHint


class Datasource(Enum):
    WHITESOURCE = 'whitesource'
    PROTECODE = 'protecode'
    CHECKMARX = 'checkmarx'
    CLAMAV = 'clamav'


@dataclasses.dataclass
class ComplianceIssueId:
    componentName: str
    componentVersion: str
    artifact: cm.Artifact


@dataclasses.dataclass
class ComplianceIssueMeta:
    datasource: Datasource
    creationDate: datetime.datetime
    uuid: str


@dataclasses.dataclass
class ComplianceIssue:
    id: ComplianceIssueId
    meta: ComplianceIssueMeta
    data: dict
