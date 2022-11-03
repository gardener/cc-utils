import dataclasses
import datetime

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


@dataclasses.dataclass(frozen=True) # TODO: deduplicate w/ modelclass in delivery-service/yp.py
class Sprint:
    name: str
    end_date: datetime.datetime
    release_decision: datetime.datetime
    rtc: datetime.datetime
    canary_freeze: datetime.datetime

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=Sprint,
            data=raw,
            config=dacite.Config(
                type_hooks={datetime.datetime: _parse_datetime_if_present},
            ),
        )


@dataclasses.dataclass(frozen=True) # deduplicate w/ modelclass in delivery-service/osinfo/model.py
class OsReleaseInfo:
    name: str
    greatest_version: str | None = None
    eol_date: datetime.date | None = None

    @property
    def parsed_version(self) -> awesomeversion.AwesomeVersion:
        return awesomeversion.AwesomeVersion(self.name)

    def reached_eol(self, ref_date:datetime.date=None):
        if not ref_date:
            ref_date = datetime.date.today()

        return self.eol_date < ref_date

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=OsReleaseInfo,
            data=raw,
            config=dacite.Config(
                type_hooks={datetime.date | None: _parse_date_if_present},
            ),
        )
