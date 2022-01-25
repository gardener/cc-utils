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
class ArtefactReference:
    componentName: str
    componentVersion: str
    artefact: typing.Union[
        cm.Resource,
        cm.ComponentSource,
    ]


@dataclasses.dataclass
class ComplianceMetadata:
    datasource: str
    creationDate: typing.Union[datetime.datetime, str]


@dataclasses.dataclass
class ComplianceData:
    artefact: ArtefactReference
    meta: ComplianceMetadata
    data: dict

    @staticmethod
    def create(
        artefact: typing.Union[cm.Resource, cm.SourceReference],
        component: cm.Component,
        type: str,
        data: dict,
    ):
        '''
        convenient method to create ComplianceData
        type: metadata type (implies expected data structure)
        data: type-specific compliance data
        '''
        ar = ArtefactReference(
            componentName=component.name,
            componentVersion=component.version,
            artefact=artefact,
        )
        cm = ComplianceMetadata(
            datasource=type,
            creationDate=datetime.datetime.now().isoformat(),
        )
        return ComplianceData(
            artefact=ar,
            data=data,
            meta=cm
        )
