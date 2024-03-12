import dataclasses
import datetime
import enum

import awesomeversion
import dacite
import dateutil.parser

import dso.model


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
    componentName: str | None
    componentVersion: str | None
    artefactName: str | None
    artefactKind: str
    artefactVersion: str | None
    artefactType: str
    artefactExtraId: dict

    @staticmethod
    def normalise_artefact_extra_id(
        artefact_extra_id: dict[str, str],
        artefact_version: str=None,
    ) -> str:
        '''
        generate stable representation of `artefact_extra_id` and remove `version` key if
        the specified version is identical to the given artefact version

        sorted by key in alphabetical order and concatinated following pattern:
        key1:value1_key2:value2_ ...
        '''
        if (version := artefact_extra_id.get('version')) and version == artefact_version:
            del artefact_extra_id['version']

        s = sorted(artefact_extra_id.items(), key=lambda items: items[0])
        return '_'.join([':'.join(values) for values in s])


@dataclasses.dataclass(frozen=True)
class ArtefactMetadata:
    # this is _almost_ (but not quite) dso.model.ArtefactMetadata :(. Namely, the structure
    # of the ArtefactId differs and there is an additional `type` str.
    artefactId: ComponentArtefactId
    type: str
    meta: dso.model.Metadata
    data: (
        dso.model.StructureInfo
        | dso.model.LicenseFinding
        | dso.model.VulnerabilityFinding
        | dso.model.OsID
        | dso.model.MalwareSummary
        | dso.model.CodecheckSummary
        | dso.model.ComplianceSnapshot
        | dso.model.CustomRescoring
        | dict
    )
    id: int | None = None
    discovery_date: datetime.date | None = None

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=ArtefactMetadata,
            data=raw,
            config=dacite.Config(
                type_hooks={
                    datetime.datetime: datetime.datetime.fromisoformat,
                    datetime.date: lambda date: datetime.datetime.strptime(date, '%Y-%m-%d').date()
                        if date else None,
                },
            ),
        )

    def to_dso_model_artefact_metadata(self) -> dso.model.ArtefactMetadata:
        return dso.model.ArtefactMetadata(
            artefact=dso.model.ComponentArtefactId(
                component_name=self.artefactId.componentName,
                component_version=self.artefactId.componentVersion,
                artefact=dso.model.LocalArtefactId(
                    artefact_name=self.artefactId.artefactName,
                    artefact_version=self.artefactId.artefactVersion,
                    artefact_type=self.artefactId.artefactType,
                    artefact_extra_id=self.artefactId.artefactExtraId,
                ),
                artefact_kind=self.artefactId.artefactKind,
            ),
            meta=self.meta,
            data=self.data,
            id=self.id,
            discovery_date=self.discovery_date,
        )


class StatusType(enum.StrEnum):
    ERROR = enum.auto()
    INFO = enum.auto()


@dataclasses.dataclass(frozen=True) # TODO: deduplicate with model-class delivery-service
class Status:
    type: StatusType
    msg: str
