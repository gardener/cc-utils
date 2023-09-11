import dataclasses
import datetime

import dso.model

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


@dataclasses.dataclass(frozen=True)
class ComponentArtefactId:
    componentName: str
    componentVersion: str
    artefactName: str
    artefactKind: str
    artefactVersion: str
    artefactType: str
    artefactExtraId: dict


@dataclasses.dataclass(frozen=True)
class ArtefactMetadata:
    # this is _almost_ (but not quite) dso.model.ArtefactMetadata :(. Namely, the structure
    # of the ArtefactId differs and there is an additional `type` str.
    artefactId: ComponentArtefactId
    type: str
    meta: dso.model.Metadata
    data: (
        dso.model.GreatestCVE
        | dso.model.LicenseSummary
        | dso.model.ComponentSummary
        | dso.model.OsID
        | dso.model.MalwareSummary
        | dso.model.FilesystemPaths
        | dso.model.CodecheckSummary
        | dict
    )

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=ArtefactMetadata,
            data=raw,
            config=dacite.Config(
                type_hooks={datetime.datetime: datetime.datetime.fromisoformat},
            ),
        )
