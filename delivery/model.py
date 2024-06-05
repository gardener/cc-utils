import dataclasses
import datetime
import enum

import awesomeversion
import dacite
import dateutil.parser


def _parse_datetime_if_present(date: str):
    if not date:
        return None
    return dateutil.parser.isoparse(date)


def _parse_date_if_present(date: str):
    if not date:
        return None
    return dateutil.parser.isoparse(date).date()


@dataclasses.dataclass # TODO: deduplicate w/ modelclass in delivery-service
class GithubUser:
    username: str
    github_hostname: str


@dataclasses.dataclass(frozen=True)
class SprintDate:
    name: str
    display_name: str
    value: datetime.datetime


@dataclasses.dataclass(frozen=True) # TODO: deduplicate w/ modelclass in delivery-service/yp.py
class Sprint:
    name: str
    dates: frozenset[SprintDate]

    def find_sprint_date(
        self,
        name: str,
        absent_ok: bool = False,
    ) -> SprintDate | None:
        for sprint_date in self.dates:
            if sprint_date.name == name:
                return sprint_date

        if absent_ok:
            return None

        raise RuntimeError(f'did not find {name=} in {self=}')

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=Sprint,
            data=raw,
            config=dacite.Config(
                type_hooks={datetime.datetime: _parse_datetime_if_present},
                cast=[frozenset],
            ),
        )


@dataclasses.dataclass(frozen=True)
class OsReleaseInfo:
    name: str
    reached_eol: bool
    greatest_version: str | None = None
    eol_date: datetime.date | None = None

    @property
    def parsed_version(self) -> awesomeversion.AwesomeVersion:
        return awesomeversion.AwesomeVersion(self.name)

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=OsReleaseInfo,
            data=raw,
            config=dacite.Config(
                type_hooks={datetime.date | None: _parse_date_if_present},
            ),
        )


class StatusType(enum.StrEnum):
    ERROR = enum.auto()
    INFO = enum.auto()


@dataclasses.dataclass(frozen=True) # TODO: deduplicate with model-class delivery-service
class Status:
    type: StatusType
    msg: str


@dataclasses.dataclass(frozen=True)
class GitHubAuthCredentials:
    api_url: str
    auth_token: str
