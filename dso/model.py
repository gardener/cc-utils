import dataclasses
import datetime
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


class Datasource:
    WHITESOURCE = 'whitesource'
    PROTECODE = 'protecode'
    CHECKMARX = 'checkmarx'
    CLAMAV = 'clamav'


@dataclasses.dataclass
class ArtifactReference:
    componentName: str
    componentVersion: str
    artifact: typing.Union[
        cm.Resource,
        cm.ComponentSource,
    ]


@dataclasses.dataclass
class ComplianceIssueMetadata:
    datasource: str
    creationDate: typing.Union[datetime.datetime, str]
    uuid: str


@dataclasses.dataclass
class ComplianceIssue:
    artifact: ArtifactReference
    meta: ComplianceIssueMetadata
    data: dict
