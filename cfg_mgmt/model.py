import dataclasses
import datetime
import dateutil.parser
import enum
import os
import re
import typing

import dacite
import pytimeparse

import ci.util
import model.base as mb


class PolicyType(enum.Enum):
    MAX_AGE = 'max_age'


class RotationMethod(enum.Enum):
    MANUAL = 'manual'
    AUTOMATED = 'automated'


@dataclasses.dataclass
class CfgPolicy:
    '''
    re-usable policies that are intended to be assigned to configuration elements
    '''
    name: str
    max_age: typing.Optional[str]
    type: PolicyType = PolicyType.MAX_AGE
    rotation_method: RotationMethod = RotationMethod.MANUAL
    comment: typing.Optional[str] = None

    def check(self, last_update: datetime.date) -> bool:
        '''
        returns `True` if policy is fulfilled, `False` if it is violated
        hard-coded to only allow PolicyType.MAX_AGE for now
        '''
        if self.max_age is None:
            return True # max_age being None means there is no expiry date

        max_age_seconds = pytimeparse.parse(self.max_age)

        today = datetime.datetime.now()
        latest_required_update_date = last_update + datetime.timedelta(seconds=max_age_seconds)

        return today < latest_required_update_date


@dataclasses.dataclass
class CfgTarget:
    type: str
    name: str

    def matches(
        self,
        element: typing.Union[str, mb.NamedModelElement],
        type: typing.Optional[str]=None,
    ) -> bool:
        '''
        checks whether the given model element matches this target. Model elements can be
        given in two forms:

        1. as only argument (-> element). In this case, `type` must not be passed (determined
           from the passed element)
        2. as two arguments (element being the element's name, type being the element's type's name)
           in the second form, both arguments must be strings
        '''
        if isinstance(element, mb.NamedModelElement):
            if type is not None:
                raise ValueError('if element is `NamedModelElement`, type must not be passed')

            element_name = element._name
            element_type = element._type_name
        else:
            if not type and element:
                raise ValueError('both element name and type name must be passed')

            element_name = ci.util.check_type(element, str)
            element_type = ci.util.check_type(type, str)

        type_matches = re.fullmatch(self.type, element_type)

        if not type_matches:
            return False

        name_matches = re.fullmatch(self.name, element_name)

        if not name_matches:
            return False

        return True # both type and name match


@dataclasses.dataclass
class CfgRule:
    '''
    configuration rules that map cfg policies to configuration elements
    '''
    targets: list[CfgTarget]
    policy: str

    def  matches(
        self,
        element: typing.Union[str, mb.NamedModelElement],
        type: typing.Optional[str]=None,
    ):
        for t in self.targets:
            if t.matches(element=element, type=type):
                return True

        return False


class CfgResponsibleType(enum.Enum):
    GITHUB = 'github'
    EMAIL = 'email'


# hash used to determine compliance rate for a responsible
@dataclasses.dataclass(unsafe_hash=True)
class CfgResponsible:
    name: str
    type: CfgResponsibleType


@dataclasses.dataclass
class CfgQueueEntry:
    target: CfgTarget
    deleteAfter: str
    secretId: dict

    def to_be_deleted(
        self,
        timestamp: datetime.datetime,
    ) -> bool:
        return timestamp > dateutil.parser.isoparse(self.deleteAfter)


@dataclasses.dataclass
class CfgStatus:
    target: CfgTarget
    credential_update_timestamp: str

    def  matches(
        self,
        element: typing.Union[str, mb.NamedModelElement],
        type: typing.Optional[str]=None,
    ):
        return self.target.matches(element=element, type=type)


@dataclasses.dataclass
class CfgResponsibleMapping:
    targets: list[CfgTarget]
    responsibles: list[CfgResponsible]

    def  matches(
        self,
        element: typing.Union[str, mb.NamedModelElement],
        type: typing.Optional[str]=None,
    ):
        for t in self.targets:
            if t.matches(element=element, type=type):
                return True

        return False


@dataclasses.dataclass
class CfgMetadata:
    '''
    stores full cfg-element metadata from a cfg-dir
    '''
    policies: tuple[CfgPolicy]
    rules: tuple[CfgRule]
    responsibles: tuple[CfgResponsibleMapping]
    statuses: list[CfgStatus]
    queue: list[CfgQueueEntry]


class CfgStatusEvaluationAspects(enum.Enum):
    ASSIGNED_RULE_REFERS_TO_UNDEFINED_POLICY = 'assignedRuleRefersToUndefinedPolicy'
    CREDENTIALS_OUTDATED = 'credentialsOutdated'
    NO_RESPONSIBLE = 'noResponsible'
    NO_RULE = 'noRule'
    NO_STATUS = 'noStatus'


@dataclasses.dataclass(frozen=True)
class CfgStatusEvaluationResult:
    fullyCompliant: bool
    hasResponsible: bool
    hasRule: bool
    assignedRuleRefersToUndefinedPolicy: bool
    hasStatus: bool
    requiresStatus: typing.Optional[bool]
    credentialsOutdated: typing.Optional[bool]
    nonCompliantReasons: typing.List[CfgStatusEvaluationAspects]


@dataclasses.dataclass
class CfgResponsibleSummary:
    url: str
    responsible: CfgResponsible
    compliantElementsCount: int = 0
    noncompliantElementsCount: int = 0


@dataclasses.dataclass
class CfgStorageSummary:
    '''
    represents a compliance summary for a cfg_storage (url)
    '''
    url: str
    noRuleAssigned: list
    noStatus: list
    assignedRuleRefersToUndefinedPolicy: list
    noResponsibleAssigned: list
    credentialsOutdated: list
    credentialsNotOutdated: list
    fullyCompliant: list
    compliantElementsCount: int = 0
    noncompliantElementsCount: int = 0


cfg_policies_fname = 'config_policies.yaml'
cfg_responsibles_fname = 'config_responsibles.yaml'
cfg_status_fname = 'config_status.yaml'
cfg_queue_fname = 'config_queue.yaml'
container_registry_fname = 'container_registry.yaml'


def _parse_cfg_policies_file(path: str):
    raw = ci.util.parse_yaml_file(path=path)

    # document expected structure
    return {
        'policies': raw['policies'],
        'rules': raw['rules'],
    }


def _parse_cfg_responsibles_file(path: str):
    raw = ci.util.parse_yaml_file(path=path)

    # document expected structure
    return {
        'responsibles': raw['responsibles'],
    }


def _parse_cfg_status_file(path: str):
    raw = ci.util.parse_yaml_file(path=path)

    # document expected structure
    return {
        'config_status': raw['config_status'],
    }


def _parse_cfg_queue_file(path: str):
    raw = ci.util.parse_yaml_file(path=path)

    # document expected structure
    return {
        'rotation_queue': raw['rotation_queue'],
    }


def cfg_policies(policies: list[dict]) -> list[CfgPolicy]:
    if isinstance(policies, dict):
        policies = policies['policies']

    if not policies:
        policies = []

    return [
        dacite.from_dict(
            data_class=CfgPolicy,
            data=policy_dict,
            config=dacite.Config(
                cast=[PolicyType, RotationMethod],
            )
        ) for policy_dict in policies
    ]


def cfg_rules(rules: list[dict]) -> list[CfgRule]:
    if isinstance(rules, dict):
        rules = rules['rules']

    if not rules:
        rules = []

    return [
        dacite.from_dict(
            data_class=CfgRule,
            data=rule_dict,
        ) for rule_dict in rules
    ]


def cfg_responsibles(responsibles: list[dict]) -> list[CfgResponsibleMapping]:
    if isinstance(responsibles, dict):
        responsibles = responsibles['responsibles']

    if not responsibles:
        responsibles = []

    return [
        dacite.from_dict(
            data_class=CfgResponsibleMapping,
            data=responsible_dict,
            config=dacite.Config(
                cast=[CfgResponsibleType],
            )
        ) for responsible_dict in responsibles
    ]


def cfg_status(status: list[dict]) -> list[CfgStatus]:
    if isinstance(status, dict):
        status = status['config_status']

    if not status:
        status = []

    return [
        dacite.from_dict(
            data_class=CfgStatus,
            data=status_dict,
        ) for status_dict in status
    ]


def cfg_queue(queue: list[dict]) -> list[CfgQueueEntry]:
    if isinstance(queue, dict):
        queue = queue['rotation_queue']

    if not queue:
        queue = []

    return [
        dacite.from_dict(
            data_class=CfgQueueEntry,
            data=queue_dict,
        ) for queue_dict in queue
    ]


def cfg_metadata_from_cfg_dir(cfg_dir: str):
    policies = _parse_cfg_policies_file(os.path.join(cfg_dir, cfg_policies_fname))
    responsibles = _parse_cfg_responsibles_file(os.path.join(cfg_dir, cfg_responsibles_fname))
    statuses = _parse_cfg_status_file(os.path.join(cfg_dir, cfg_status_fname))
    queue = _parse_cfg_queue_file(os.path.join(cfg_dir, cfg_queue_fname))

    return CfgMetadata(
        policies=tuple(cfg_policies(policies['policies'])),
        rules=tuple(cfg_rules(policies['rules'])),
        responsibles=tuple(cfg_responsibles(responsibles['responsibles'])),
        statuses=list(cfg_status(statuses['config_status'])),
        queue=list(cfg_queue(queue['rotation_queue'])),
    )
