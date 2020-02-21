import datetime
import dataclasses
from enum import Enum
import typing


class ScanResult(Enum):
    FINISHED = 7
    FAILED = 9


class CustomFieldNames(Enum):
    ZIP_HASH = 'zip_hash'
    COMMIT_HASH = 'commit_hash'


@dataclasses.dataclass
class ScanStatus:
    id: int
    name: str


@dataclasses.dataclass
class ScanType:
    id: int
    value: str


@dataclasses.dataclass
class ScanSettings:
    projectId: int
    isIncremental: bool = True
    isPublic: bool = True
    forceScan: bool = True
    comment: str = ""


@dataclasses.dataclass
class AuthResponse:
    access_token: str
    expires_in: int
    token_type: str
    expires_at: datetime.datetime = None

    def is_valid(self):
        return datetime.datetime.now() > self.expires_at


@dataclasses.dataclass
class ScanResponse:
    owner: str
    id: int
    scanRisk: int
    scanRiskSeverity: int
    status: ScanStatus
    scanType: ScanType
    isIncremental: bool
    owningTeamId: str


@dataclasses.dataclass
class CustomField:
    id: int
    value: str


@dataclasses.dataclass
class ProjectDetails:
    id: int
    teamId: str
    name: str
    customFields: typing.List[CustomField] = dataclasses.field(default_factory=list)

    def get_custom_field(self, attribute_key: str, pop: bool = False):
        for cf in self.customFields:
            key, value = cf.value.split(':', 1)
            if key == attribute_key:
                if pop:
                    self.customFields.remove(cf)
                return value

    def set_custom_field(self, attribute_key: str, value: str):
        self.get_custom_field(attribute_key=attribute_key, pop=True)
        self.customFields.append(CustomField(len(self.customFields), f'{attribute_key}:{value}'))
