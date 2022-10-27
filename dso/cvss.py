import dataclasses
import enum

'''
utils/model classes for CVSS (https://www.first.org/cvss/user-guide)
'''


class AccessVector(enum.Enum):
    NETWORK = 'N'
    ADJACENT = 'A'
    LOCAL = 'N'
    PYSICAL = 'P'


class AttackComplexity(enum.Enum):
    LOW = 'L'
    HIGH = 'H'


class PrivilegesRequired(enum.Enum):
    NONE = 'N'
    LOW = 'L'
    HIGH = 'H'


class UserInteraction(enum.Enum):
    NONE = 'N'
    REQUIRED = 'R'


class Scope(enum.Enum):
    UNCHANGED = 'U'
    CHANGED = 'C'


class Confidentiality(enum.Enum):
    NONE = 'N'
    LOW = 'L'
    HIGH = 'H'


class Integrity(enum.Enum):
    NONE = 'N'
    LOW = 'L'
    HIGH = 'H'


class Availability(enum.Enum):
    NONE = 'N'
    LOW = 'L'
    HIGH = 'H'


@dataclasses.dataclass
class CVSSV3:
    access_vector: AccessVector
    attack_complexity: AttackComplexity
    user_interaction: UserInteraction
    scope: Scope
    confidentiality: Confidentiality
    integrity: Integrity
    availability: Availability

    @staticmethod
    def parse(cvss: str) -> 'CVSSV3':
        parts = {e.split(':')[0]: e.split(':')[1] for e in cvss.split('/')}
        return CVSSV3(
            access_vector=AccessVector(parts['AV']),
            attack_complexity=AttackComplexity(parts['AC']),
            user_interaction=UserInteraction(parts['UI']),
            scope=Scope(parts['S']),
            confidentiality=Confidentiality(parts['C']),
            integrity=Integrity(parts['I']),
            availability=Availability(parts['A']),
        )
