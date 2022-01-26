import dataclasses
import datetime
import enum
import re
import typing

import dacite
import pytimeparse

import ci.util
import model.base as mb


class PolicyType(enum.Enum):
    MAX_AGE = 'max_age'


@dataclasses.dataclass
class CfgPolicy:
    '''
    re-usable policies that are intended to be assigned to configuration elements
    '''
    name: str
    max_age: typing.Optional[str]
    type: PolicyType = PolicyType.MAX_AGE
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


@dataclasses.dataclass
class CfgResponsible:
    name: str
    type: CfgResponsibleType


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


cfg_policies_fname = 'config_policies.yaml'
cfg_responsibles_fname = 'config_responsibles.yaml'
cfg_status_fname = 'config_status.yaml'


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


def cfg_policies(policies: list[dict]) -> list[CfgPolicy]:
    if isinstance(policies, dict):
        policies = policies['policies']

    return [
        dacite.from_dict(
            data_class=CfgPolicy,
            data=policy_dict,
            config=dacite.Config(
                cast=[PolicyType],
            )
        ) for policy_dict in policies
    ]


def cfg_rules(rules: list[dict]) -> list[CfgRule]:
    if isinstance(rules, dict):
        rules = rules['rules']

    return [
        dacite.from_dict(
            data_class=CfgRule,
            data=rule_dict,
        ) for rule_dict in rules
    ]


def cfg_responsibles(responsibles: list[dict]) -> list[CfgResponsibleMapping]:
    if isinstance(responsibles, dict):
        responsibles = responsibles['responsibles']

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

    return [
        dacite.from_dict(
            data_class=CfgStatus,
            data=status_dict,
        ) for status_dict in status
    ]
