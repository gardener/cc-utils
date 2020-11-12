import dataclasses
import enum
import typing


@dataclasses.dataclass(frozen=True)
class PathRegexes:
    include_paths: typing.List[str] = dataclasses.field(default_factory=list)
    exclude_paths: typing.List[str] = dataclasses.field(default_factory=list)


class ScanPolicy(enum.Enum):
    SCAN = 'scan'
    SKIP = 'skip'


class ScanLabelName(enum.Enum):
    BINARY_SCAN = 'cloud.gardener.cnudie/sdo/scanning-hints/binary/v1'
    SOURCE_SCAN = 'cloud.gardener.cnudie/sdo/scanning-hints/source_analysis/v1'
    SOURCE_ID = 'cloud.gardener.cnudie/sdo/scanning-hints/source_id/v1'


@dataclasses.dataclass(frozen=True)
class ScanLabelValue:
    policy: ScanPolicy
    path_config: typing.Optional[PathRegexes]


@dataclasses.dataclass(frozen=True)
class SourceScanHint(ScanLabelValue):
    pass


@dataclasses.dataclass(frozen=True)
class BinaryScanHint(ScanLabelValue):
    pass


@dataclasses.dataclass(frozen=True)
class ScanLabel:
    name: str
    value: ScanLabelValue
