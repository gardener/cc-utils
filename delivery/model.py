import dataclasses
import datetime

import dacite
import dateutil.parser


@dataclasses.dataclass # TODO: deduplicate w/ modelclass in delivery-service
class GithubUser:
    username: str
    github_hostname: str


@dataclasses.dataclass(frozen=True) # TODO: deduplicate w/ modelclass in delivery-service/yp.py
class Sprint:
    name: str
    end_date: datetime.date
    release_decision: datetime.date
    rtc: datetime.date
    canary_freeze: datetime.date

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=Sprint,
            data=raw,
            config=dacite.Config(
                type_hooks={datetime.date: lambda d: dateutil.parser.isoparse(d).date()},
            ),
        )
