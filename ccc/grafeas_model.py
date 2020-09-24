import datetime

import dacite
import dateutil.parser

# keep import short for convenience
from dataclasses import (
    dataclass,
    field,
)

from enum import (
    Enum,
    IntEnum,
)
from typing import(
    List,
    Optional,
)


class Severity(IntEnum):
    CRITICAL = 5
    HIGH = 4
    MEDIUM = 3
    LOW = 2
    MINIMAL = 1
    SEVERITY_UNSPECIFIED = 0


class OccurrenceKind(Enum):
    DISCOVERY = 'DISCOVERY'
    VULNERABILITY = 'VULNERABILITY'


class PackageVersionKind(Enum):
    NORMAL = 'NORMAL'
    MAXIMUM = 'MAXIMUM'


class ContinuousAnalysis(Enum):
    INACTIVE = 'INACTIVE'
    ACTIVE = 'ACTIVE'
    CONTINUOUS_ANALYSIS_UNSPECIFIED =  'CONTINUOUS_ANALYSIS_UNSPECIFIED'


class AnalysisStatus(Enum):
    FINISHED_SUCCESS = 'FINISHED_SUCCESS'
    PENDING = 'PENDING'
    SCANNING = 'SCANNING'
    FINISHED_FAILED = 'FINISHED_FAILED'
    FINISHED_UNSUPPORTED = 'FINISHED_UNSUPPORTED'
    ANALYSIS_STATUS_UNSPECIFIED = 'ANALYSIS_STATUS_UNSPECIFIED'


@dataclass
class PackageVersion:
    kind: PackageVersionKind
    name: Optional[str]
    revision: Optional[str]
    epoch: Optional[int]
    fullName: Optional[str]


@dataclass
class PackageIssue:
    affectedCpeUri: str
    affectedPackage: str
    affectedVersion: PackageVersion
    fixedCpeUri: str
    fixedPackage: str
    fixedVersion: PackageVersion
    fixAvailable: bool = False


@dataclass
class Discovery:
    analysisStatus: Optional[AnalysisStatus]
    analysisStatusError: Optional[dict]
    continuousAnalysis: Optional[ContinuousAnalysis]


@dataclass
class Vulnerability:
    packageIssue: List[PackageIssue]
    severity: Optional[Severity]
    relatedUrls: Optional[List[dict]]
    longDescription: Optional[str]
    shortDescription: Optional[str]
    effectiveSeverity: Severity = Severity.SEVERITY_UNSPECIFIED
    fixAvailable: bool = False
    cvssScore: float = 0.0 # In line with google's UI display


@dataclass
class Resource:
    uri: str
    contentHash: Optional[dict]
    name: Optional[str]


@dataclass
class Occurrence:
    createTime: datetime.datetime
    kind: OccurrenceKind
    name: str
    noteName: str
    resourceUri: str
    updateTime: datetime.datetime
    vulnerability: Optional[Vulnerability]
    discovery: Optional[Discovery]


@dataclass
class ListOccurrencesResponse:
    nextPageToken: Optional[str]
    occurrences: Optional[List[Occurrence]] = field(default_factory=list)

    @staticmethod
    def parse(response):
        return dacite.from_dict(
            data_class=ListOccurrencesResponse,
            data=response,
            config=dacite.Config(
                type_hooks={
                    Severity: lambda x: Severity[x],
                    datetime.datetime: lambda x: dateutil.parser.isoparse(x),
                },
                cast=[Enum],
            ),
        )
