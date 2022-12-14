import datetime
import dataclasses
from enum import Enum, IntEnum
from functools import total_ordering
import typing

import github.compliance.model as gcm


class ScanStatusValues(Enum):
    NEW = 1
    PRE_SCAN = 2
    QUEUED = 3
    SCANNING = 4
    POST_SCAN = 6
    FINISHED = 7
    CANCELED = 8
    FAILED = 9
    SOURCE_PULLING_AND_DEPLOYMENT = 10


class CustomFieldKeys(Enum):
    COMPONENT_NAME = 4
    VERSION = 5
    HASH = 6


@dataclasses.dataclass
class CustomField:
    id: int
    value: str


@total_ordering
class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def from_str(cls, s: str) -> IntEnum | None:
        su = s.upper()
        if su == 'INFO':
            return Severity.INFO
        elif su == 'LOW':
            return Severity.LOW
        elif su == 'MEDIUM':
            return Severity.MEDIUM
        elif su == 'HIGH':
            return Severity.HIGH
        else:
            return None

    def __str__(self):
        return self.name.lower()


@dataclasses.dataclass
class ProjectDetails:
    id: int
    teamId: int
    name: str
    customFields: typing.List[CustomField] = dataclasses.field(default_factory=list)

    def get_custom_field(self, attribute_key: CustomFieldKeys, pop: bool = False):
        if not isinstance(attribute_key, CustomFieldKeys):
            raise ValueError(attribute_key)

        for cf in self.customFields:
            if cf.id == attribute_key.value:
                if pop:
                    self.customFields.remove(cf)
                return cf.value

    def set_custom_field(self, attribute_key: CustomFieldKeys, value: str):
        self.get_custom_field(attribute_key=attribute_key, pop=True)
        self.customFields.append(CustomField(id=attribute_key.value, value=value))


@dataclasses.dataclass
class ScanStatusDetails:
    stage: str
    step: str


@dataclasses.dataclass
class ScanStatus:
    id: int
    name: str
    details: ScanStatusDetails


@dataclasses.dataclass
class AuthResponse:
    access_token: str
    expires_in: int
    token_type: str
    expires_at: datetime.datetime = None

    def is_valid(self):
        return datetime.datetime.now() < self.expires_at


@dataclasses.dataclass
class ScanDateAndTime:
    startedOn: str
    finishedOn: typing.Optional[str]


@dataclasses.dataclass
class ScanResponse:
    owner: str
    id: int
    scanRisk: int
    scanRiskSeverity: int
    status: ScanStatus
    isIncremental: bool
    owningTeamId: int
    dateAndTime: typing.Optional[ScanDateAndTime] = None

    def status_value(self):
        return ScanStatusValues(self.status.id)


@dataclasses.dataclass
class ScanStatistic:
    highSeverity: int
    mediumSeverity: int
    lowSeverity: int
    infoSeverity: int
    statisticsCalculationDate: str


@dataclasses.dataclass
class ScanSettings:
    projectId: int
    isIncremental: bool = True
    isPublic: bool = True
    forceScan: bool = True
    comment: str = ""


# below types are not used for http body deserialization
@dataclasses.dataclass
class ScanResult(gcm.ScanResult):
    """
    ScanResult is a data container for a scan result for a component version.
    Successful scans must provide not None values for `scan_response`, `scan_statistic`, and
    `report_url`.
    """
    artifact_name: str
    project_id: int
    scan_response: ScanResponse | None
    scan_statistic: ScanStatistic | None
    report_url: str | None
    overview_url: str


@dataclasses.dataclass
class FinishedScans:
    failed_scans: typing.List[ScanResult] = dataclasses.field(default_factory=list)
    scans: typing.List[ScanResult] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class FailedScan:
    artifact_name: str
