import dataclasses
import datetime
import enum

import dacite
import dateutil.parser


def _parse_datetime_if_present(date: str):
    if not date:
        return None
    return dateutil.parser.isoparse(date)


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
