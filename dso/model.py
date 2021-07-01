from dataclasses import dataclass
from enum import Enum
import typing

import dso.labels
import gci.componentmodel as cm


class ArtifactType(Enum):
    RESOURCE = 'resource'
    SOURCE = 'source'


# abstraction of component model v2 source and resource
@dataclass
class ScanArtifact:
    name: str
    access: typing.Union[
        cm.OciAccess,
        cm.GithubAccess,
        cm.HttpAccess,
        cm.ResourceAccess,
    ]
    label: dso.labels.ScanningHint
    componentName: str
    componentVersion: str
    artifactName: str
    artifactVersion: str
    artifactType: ArtifactType
