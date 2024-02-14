# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import enum
import logging
import typing

import dacite
import pydash

from concourse.model.base import (
    AttributeSpec,
    ModelBase,
)
import cnudie.iter
import reutil

logger = logging.getLogger(__name__)


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
) -> typing.Callable[[cnudie.iter.Node], bool]:
    configs = tuple(configs) if configs else ()
    if not configs:
        def match_all(node: cnudie.iter.Node):
            return True

        return match_all

    # A filter for several matching configs is the combination of its constituent filters joined
    # with a boolean OR
    filters_from_configs = [
        filter_for_matching_config(
            config=config,
        ) for config in configs
    ]
    return lambda node: any(
        filter_func(node) for filter_func in filters_from_configs
    )


def filter_for_matching_config(
    config: MatchingConfig,
) -> typing.Callable[[cnudie.iter.Node], bool]:
    # A filter for a single matching configs is the combination of the filters for its rules joined
    # with a boolean AND
    rule_filters = [
        filter_for_rule(
            rule=rule,
        ) for rule in config.rules
    ]
    return lambda node: all(
        filter_func(node) for filter_func in rule_filters
    )


def filter_for_rule(
    rule: ConfigRule,
) -> typing.Callable[[cnudie.iter.Node], bool]:
    def to_str(value):
        if isinstance(value, str):
            return value
        elif isinstance(value, bool):
            return 'true' if value else 'false'
        elif isinstance(value, int) or isinstance(value, float):
            return str(value)
        elif isinstance(value, enum.Enum):
            return value.value
        else:
            logger.warning(f'selected {value=} is no scalar - matching will likely fail')
            return str(value)

    match rule.matching_semantics:
        case ComponentFilterSemantics.INCLUDE:
            re_filter = reutil.re_filter(
                include_regexes=[rule.expression],
                value_transformation=to_str,
            )
        case ComponentFilterSemantics.EXCLUDE:
            re_filter = reutil.re_filter(
                exclude_regexes=[rule.expression],
                value_transformation=to_str,
            )
        case _:
            raise NotImplementedError(rule.matching_semantics)

    def filter_func(node: cnudie.iter.Node):
        match rule.target.split('.'):
            case ['component', *tail]:
                return re_filter(pydash.get(node.component, tail))
            case ['resource', *tail]:
                if not isinstance(node, cnudie.iter.ResourceNode):
                    return True
                return re_filter(pydash.get(node.resource, tail))
            case ['source', *tail]:
                if not isinstance(node, cnudie.iter.SourceNode):
                    return True
                return re_filter(pydash.get(node.source, tail))
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
