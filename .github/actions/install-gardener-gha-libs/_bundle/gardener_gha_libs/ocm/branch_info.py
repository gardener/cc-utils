import dataclasses
import datetime
import enum
import re
import typing

import dacite


def convert_to_timedelta(
    time: str,
) -> datetime.timedelta:
    seconds_per_unit = {
        's': 1,
        'sec': 1,
        'm': 60,
        'min': 60,
        'h': 60 * 60,
        'hr': 60 * 60,
        'd': 60 * 60 * 24,
        'w': 60 * 60 * 24 * 7,
        'y': 60 * 60 * 24 * 365,
        'yr': 60 * 60 * 24 * 365,
    }
    unit = None

    if not (match := re.match(r'([0-9]+)\s*([a-z]+)', str(time).strip(), re.IGNORECASE)):
        raise ValueError(f'invalid time format {time=}, expected `<time-int> <unit>`')

    time, unit = match.groups()

    try:
        seconds = int(time) * seconds_per_unit[unit]
    except KeyError:
        raise ValueError(f'invalid {unit=}, known units: {seconds_per_unit.keys()}')

    return datetime.timedelta(seconds=seconds)


class VersionParts(enum.StrEnum):
    MAJOR = 'major'
    MINOR = 'minor'
    PATCH = 'patch'


@dataclasses.dataclass
class BranchPolicy:
    significant_part: VersionParts = VersionParts.MINOR
    supported_versions_count: int | None = None
    release_cadence: str | None = None

    def support_phase_duration(self) -> datetime.timedelta | None:
        '''
        Returns the estimated duration a branch is considered to be in "supported" state.
        '''
        if self.supported_versions_count is None or not self.release_cadence:
            return None
        return self.supported_versions_count * convert_to_timedelta(self.release_cadence)


@dataclasses.dataclass
class BranchInfo:
    '''
    model-class for "branch-info" expected (by default) at `.ocm/branch-info.yaml`.
    '''
    branch_policy: BranchPolicy = dataclasses.field(default_factory=BranchPolicy)
    release_branch_template: str = 'release-v$major.$minor' # e.g. release-v1.0

    def __post_init__(self):
        # replace custom "$major" placeholder with equally named regex group
        template = re.sub(
            pattern=r'\$major',
            repl=r'(?P<major>\\d+)',
            string=self.release_branch_template,
        )
        # replace custom "$minor" placeholder with equally named regex group
        template = re.sub(
            pattern=r'\$minor',
            repl=r'(?P<minor>\\d+)',
            string=template,
        )
        # replace custom "$patch" placeholder with equally named regex group
        template = re.sub(
            pattern=r'\$patch',
            repl=r'(?P<patch>\\d+)',
            string=template,
        )
        # don't interpret "." as any character but only as "."
        template = re.sub(
            pattern=r'\.',
            repl=r'\\.',
            string=template,
        )

        self.release_branch_pattern = re.compile(template)

    @staticmethod
    def from_dict(raw: dict) -> typing.Self:
        return dacite.from_dict(
            data_class=BranchInfo,
            data=raw,
            config=dacite.Config(
                cast=[enum.Enum],
                convert_key=lambda key: key.replace('_', '-'),
            ),
        )
