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
    BINARY_SCAN = 'cloud.gardener.cnudie/dso/scanning-hints/binary/v1' # deprecated
    BINARY_ID = 'cloud.gardener.cnudie/dso/scanning-hints/binary_id/v1'
    SOURCE_SCAN = 'cloud.gardener.cnudie/dso/scanning-hints/source_analysis/v1'
    SOURCE_ID = 'cloud.gardener.cnudie/dso/scanning-hints/source_id/v1'


@dataclasses.dataclass(frozen=True)
class ScanningHint:
    policy: ScanPolicy
    path_config: typing.Optional[PathRegexes]
    comment: typing.Optional[str]


@dataclasses.dataclass(frozen=True)
class SourceScanHint(ScanningHint):
    pass


@dataclasses.dataclass(frozen=True)
class BinaryScanHint(ScanningHint):
    pass


@dataclasses.dataclass(frozen=True)
class SourceIdHint(ScanningHint):
    pass
