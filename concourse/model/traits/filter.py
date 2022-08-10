# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import dataclasses
import enum
import typing

import dacite
import pydash

from concourse.model.base import (
    AttributeSpec,
    ModelBase,
)
import gci.componentmodel as cm
import reutil


class ImageFilterMixin(ModelBase):
    def matching_config(self):
        return self.raw['matching_config']


FILTER_ATTRS = (
    AttributeSpec.optional(
        name='matching_config',
        default=[],
        doc='''
        a list of configs to use for matching
        ''',
        type=list,
    ),
)


class ComponentFilterSemantics(enum.Enum):
    INCLUDE = 'include'
    EXCLUDE = 'exclude'


@dataclasses.dataclass
class ConfigRule:
    target: str
    expression: str
    matching_semantics: ComponentFilterSemantics


@dataclasses.dataclass
class MatchingConfig:
    name: str
    rules: typing.List[ConfigRule]


def filter_for_matching_configs(
    configs: typing.Collection[MatchingConfig]
) -> typing.Callable[[cm.Component, cm.Resource], bool]:
    # A filter for several matching configs is the combination of its constituent filters joined
    # with a boolean AND
    filters_from_configs = [
        filter_for_matching_config(
            config=config,
        ) for config in configs
    ]
    return lambda component, resource: all(
        filter_func(component, resource) for filter_func in filters_from_configs
    )


def filter_for_matching_config(
    config: MatchingConfig,
) -> typing.Callable[[cm.Component, cm.Resource], bool]:
    # A filter for a single matching configs is the combination of the filters for its rules joined
    # with a boolean OR
    rule_filters = [
        filter_for_rule(
            rule=rule,
        ) for rule in config.rules
    ]
    return lambda component, resource: any(
        filter_func(component, resource) for filter_func in rule_filters
    )


def filter_for_rule(
    rule: ConfigRule,
) -> typing.Callable[[cm.Component, cm.Resource], bool]:
    match rule.matching_semantics:
        case ComponentFilterSemantics.INCLUDE:
            re_filter = reutil.re_filter(
                include_regexes=[rule.expression],
                value_transformation=str
            )
        case ComponentFilterSemantics.EXCLUDE:
            re_filter = reutil.re_filter(
                exclude_regexes=[rule.expression],
                value_transformation=str
            )
        case _:
            raise NotImplementedError(rule.matching_semantics)

    def filter_func(component: cm.Component, resource:cm.Resource):
        match rule.target.split('.'):
            case ['component', *tail]:
                return re_filter(pydash.get(component, tail))
            case ['resource', *tail]:
                return re_filter(pydash.get(resource, tail))
            case _:
                raise ValueError(f"Unable to parse matching rule '{rule.target}'")

    return filter_func


def matching_configs_from_dicts(
    dicts: typing.Iterable[dict],
) -> typing.List[MatchingConfig]:
    return [
        dacite.from_dict(
            data_class=MatchingConfig,
            data=d,
            config=dacite.Config(
                cast=[ComponentFilterSemantics]
            )
        ) for d in dicts
    ]
