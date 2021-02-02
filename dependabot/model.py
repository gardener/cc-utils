import dataclasses
import enum
import github3.repos


class DependebotStatus(enum.Enum):
    ENABLED = 'enabled'
    NOT_ENABLED = 'not_enabled'
    UNKNOWN = 'unknown'  # error case, e.g. dependabot.yaml found but faulty yaml


@dataclasses.dataclass(frozen=True)
class DependabotStatusForRepo:
    repo: github3.repos.ShortRepository
    status: DependebotStatus
