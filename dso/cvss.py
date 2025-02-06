'''
utils/model classes for CVSS (https://www.first.org/cvss/user-guide)
'''
import dataclasses
import enum

import dacite


class AccessVector(enum.Enum):
    NETWORK = 'N'
    ADJACENT = 'A'
    LOCAL = 'L'
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
    privileges_required: PrivilegesRequired
    scope: Scope
    confidentiality: Confidentiality
    integrity: Integrity
    availability: Availability

    @staticmethod
    def attr_name_from_CVSS(name: str):
        if name == 'AV':
            return 'access_vector'
        elif name == 'AC':
            return 'attack_complexity'
        elif name == 'UI':
            return 'user_interaction'
        elif name == 'C':
            return 'confidentiality'
        elif name == 'I':
            return 'integrity'
        elif name == 'A':
            return 'availability'
        elif name == 'PR':
            return 'privileges_required'
        else:
            raise ValueError(name)

    @staticmethod
    def parse(cvss: str) -> 'CVSSV3':
        parts = {e.split(':')[0]: e.split(':')[1] for e in cvss.split('/')}
        return CVSSV3(
            access_vector=AccessVector(parts['AV']),
            attack_complexity=AttackComplexity(parts['AC']),
            privileges_required=PrivilegesRequired(parts['PR']),
            user_interaction=UserInteraction(parts['UI']),
            scope=Scope(parts['S']),
            confidentiality=Confidentiality(parts['C']),
            integrity=Integrity(parts['I']),
            availability=Availability(parts['A']),
        )

    @staticmethod
    def from_dict(cvss: dict) -> 'CVSSV3':
        return dacite.from_dict(
            data_class=CVSSV3,
            data=cvss,
            config=dacite.Config(
                cast=[
                    AccessVector,
                    AttackComplexity,
                    UserInteraction,
                    PrivilegesRequired,
                    Scope,
                    Confidentiality,
                    Integrity,
                    Availability,
                ],
            ),
        )

    def __str__(self) -> str:
        return (
            f'AV:{self.access_vector.value}/AC:{self.attack_complexity.value}/'
            f'PR:{self.privileges_required.value}/UI:{self.user_interaction.value}/'
            f'S:{self.scope.value}/C:{self.confidentiality.value}/'
            f'I:{self.integrity.value}/A:{self.availability.value}'
        )


class NetworkExposure(enum.Enum):
    PRIVATE = 'private'
    PROTECTED = 'protected'
    PUBLIC = 'public'


class InteractingUserCategory(enum.Enum):
    GARDENER_OPERATOR = 'gardener-operator'
    END_USER = 'end-user'


class CVENoneLowHigh(enum.Enum):
    NONE = 'none'
    LOW = 'low'
    HIGH = 'high'


@dataclasses.dataclass
class CveCategorisation:
    network_exposure: NetworkExposure | None
    authentication_enforced: bool | None
    user_interaction: InteractingUserCategory | None
    confidentiality_requirement: CVENoneLowHigh | None
    integrity_requirement: CVENoneLowHigh | None
    availability_requirement: CVENoneLowHigh | None
    comment: str | None

    @staticmethod
    def from_dict(raw: dict):
        return dacite.from_dict(
            data_class=CveCategorisation,
            data=raw,
            config=dacite.Config(cast=(enum.Enum,)),
        )
