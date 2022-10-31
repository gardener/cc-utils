import dataclasses
import enum
import functools
import inspect
import typing
import sys

import dacite

import gci.componentmodel as cm
import dso.cvss


@dataclasses.dataclass(frozen=True)
class PathRegexes:
    include_paths: typing.List[str] = dataclasses.field(default_factory=list)
    exclude_paths: typing.List[str] = dataclasses.field(default_factory=list)


class ScanPolicy(enum.Enum):
    SCAN = 'scan'
    SKIP = 'skip'


@dataclasses.dataclass(frozen=True)
class LabelValue:
    pass


@dataclasses.dataclass(frozen=True)
class Label:
    name: str
    value: LabelValue


@dataclasses.dataclass(frozen=True)
class ScanningHint(LabelValue):
    policy: ScanPolicy
    path_config: typing.Optional[PathRegexes]
    comment: typing.Optional[str]


@dataclasses.dataclass(frozen=True)
class BinaryIdScanLabel(Label):
    name = 'cloud.gardener.cnudie/dso/scanning-hints/binary_id/v1'
    _alt_name = 'cloud.gardener.cnudie/dso/scanning-hints/binary/v1' # deprecated
    value: ScanningHint


@dataclasses.dataclass(frozen=True)
class SourceScanLabel(Label):
    name = 'cloud.gardener.cnudie/dso/scanning-hints/source_analysis/v1'
    value: ScanningHint


@dataclasses.dataclass(frozen=True)
class PackageVersionHint:
    name: str
    version: str


@dataclasses.dataclass(frozen=True)
class PackageVersionHintLabel(Label):
    name = 'cloud.gardener.cnudie/dso/scanning-hints/package-versions'
    value: tuple[PackageVersionHint]


@dataclasses.dataclass(frozen=True)
class SourceProjectLabel(Label):
    name = 'cloud.gardener.cnudie/dso/scanning-hints/checkmarx-project-name/v1'
    value: str


@dataclasses.dataclass(frozen=True)
class SourceScanHint(ScanningHint):
    pass


@dataclasses.dataclass(frozen=True)
class BinaryScanHint(ScanningHint):
    pass


@dataclasses.dataclass(frozen=True)
class SourceIdHint(ScanningHint):
    pass


@dataclasses.dataclass(frozen=True)
class CveCategorisationLabel(Label):
    name = 'gardener.cloud/cve-categorisation'
    value: dso.cvss.CveCategorisation


@functools.cache
def _label_to_type() -> dict[str, Label]:
    own_module = sys.modules[__name__]
    types = tuple(t for entry
        in inspect.getmembers(own_module, inspect.isclass)
        if (t := entry[1]) != Label and issubclass(t, Label)
    )

    label_names_to_types = {}
    for t in types:
        label_names_to_types[t.name] = t
        if (n := getattr(t, '_alt_name', None)):
            label_names_to_types[n] = t

    return label_names_to_types


def deserialise_label(
    label: cm.Label | dict,
):
    if not (t := _label_to_type().get(label.name)):
        raise ValueError(f'unknown {label.name=}')

    if isinstance(label, cm.Label):
        label = {
            'name': label.name,
            'value': label.value,
        }

    return dacite.from_dict(
        data_class=t,
        data=label,
        config=dacite.Config(
            cast=(tuple, enum.Enum)
        ),
    )
