import dataclasses
import datetime
import enum
import typing

import gci.componentmodel as cm
import github.compliance.model as gcm


class ScanStatus(enum.Enum):
    SCAN_SUCCEEDED = 'scan_succeeded'
    SCAN_FAILED = 'scan_failed'


class MalwareStatus(enum.IntEnum):
    OK = 0
    FOUND_MALWARE = 1
    UNKNOWN = 2


@dataclasses.dataclass
class Meta:
    scanned_octets: int
    receive_duration_seconds: float
    scan_duration_seconds: float
    scanned_content_digest: str | None = None


@dataclasses.dataclass
class ScanResult:
    status: ScanStatus
    details: str
    malware_status: MalwareStatus
    meta: typing.Optional[Meta]
    name: str


@dataclasses.dataclass
class ClamAVVersionInfo:
    clamav_version_str: str # as returned by clamAV, example: "ClamAV 0.105.1"
    signature_version: int # seems to increase strictly monotonically by 1 each day
    signature_date: datetime.datetime


class MalwareScanState(enum.Enum):
    FINISHED_SUCCESSFULLY = 'finished_successfully'
    FINISHED_WITH_ERRORS = 'finished_with_errors'


@dataclasses.dataclass
class AggregatedScanResult:
    '''
    overall (aggregated) scan result for a scanned resource
    '''
    resource_url: str
    name: str
    malware_status: MalwareStatus
    findings: typing.Collection[ScanResult] # if empty, there were no findings
    scan_count: int # amount of scanned files
    scanned_octets: int
    scan_duration_seconds: float
    upload_duration_seconds: float
    clamav_version_info: ClamAVVersionInfo

    def summary(self, fmt:str='html') -> str:
        if not fmt == 'html':
            raise NotImplementedError(fmt)

        def details_for_finding(scan_result: ScanResult):
            return f'<li>{scan_result.name}: {scan_result.details}</li>'

        newline = '\n'

        return f'''\
          <ul>
            <li>{self.resource_url}:
              <ul>{newline.join(("- " + details_for_finding(res) for res in self.findings))}</ul>
            </li>
          </ul>
        '''


@dataclasses.dataclass
class ClamAVResourceScanResult(gcm.ScanResult):
    scan_result: AggregatedScanResult


@dataclasses.dataclass
class MalwareScanResult:
    resource: cm.Resource
    scan_state: MalwareScanState
    findings: typing.List[str]
