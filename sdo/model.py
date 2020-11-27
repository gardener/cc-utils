import dataclasses
import typing

import gci.componentmodel as cm

import sdo.labels


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
    label: sdo.labels.ScanLabelValue
