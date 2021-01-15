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
