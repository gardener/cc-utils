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


import typing

from abc import abstractmethod
from enum import Enum
from collections import namedtuple

import ci.util

from model.base import (
    ModelValidationError,
    ModelValidationMixin,
)


class EnumWithDocumentation(Enum):
    def __new__(cls, value, docstring=None):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.__doc__ = docstring
        return obj

    def __str__(self):
        return str(self.value)


EnumValueWithDocumentation = namedtuple('EnumValueWithDocumentation', ['value', 'doc'])


class AttribSpecMixin(object):
    @classmethod
    def _attribute_specs(cls):
        raise NotImplementedError

    @classmethod
    def _defaults_dict(cls):
        return AttributeSpec.defaults_dict(cls._attribute_specs())

    @classmethod
    def _optional_attributes(cls):
        return set(AttributeSpec.optional_attr_names(cls._attribute_specs())) | \
            set(AttributeSpec.deprecated_attr_names(cls._attribute_specs()))

    @classmethod
    def _required_attributes(cls):
        return set(AttributeSpec.required_attr_names(cls._attribute_specs()))

    def _apply_defaults(self, raw_dict):
        self.raw = ci.util.merge_dicts(
            self._defaults_dict(),
            raw_dict,
        )


class ModelBase(AttribSpecMixin, ModelValidationMixin):
    def __init__(self, raw_dict: dict):
        ci.util.not_none(raw_dict)

        self._apply_defaults(raw_dict=raw_dict)
        self.custom_init(self.raw)

    def custom_init(self, raw_dict: dict):
        pass

    def _children(self):
        return ()


class RequiredPolicy(Enum):
    OPTIONAL = 'optional'
    REQUIRED = 'required'
    DEPRECATED = 'deprecated'


class AttributeSpec(object):
    @staticmethod
    def optional(name, doc, default, *args, **kwargs):
        return AttributeSpec(
            name=name,
            doc=doc,
            default=default,
            required=RequiredPolicy.OPTIONAL,
            *args,
            **kwargs,
        )

    @staticmethod
    def required(name, doc, *args, **kwargs):
        return AttributeSpec(
            name=name,
            doc=doc,
            required=RequiredPolicy.REQUIRED,
            *args,
            **kwargs,
        )

    @staticmethod
    def deprecated(name, doc, *args, **kwargs):
        return AttributeSpec(
            name=name,
            doc=doc,
            required=RequiredPolicy.DEPRECATED,
            *args,
            **kwargs,
        )

    @staticmethod
    def filter_attrs(attrs: 'typing.Iterable[AttributeSpec]', required: RequiredPolicy):
        if required:
            ci.util.check_type(required, RequiredPolicy)
        else:
            # no filtering
            yield from attrs

        for attr in attrs:
            ci.util.check_type(attr, AttributeSpec)
            if attr.required_policy() is required:
                yield attr

    @staticmethod
    def select_name(attr):
        ci.util.check_type(attr, AttributeSpec)
        return attr.name()

    def select_name_and_default(attr):
        ci.util.check_type(attr, AttributeSpec)
        return attr.name(), attr.default_value()

    @staticmethod
    def required_attr_names(attrs: 'typing.Iterable[AttributeSpec]'):
        yield from map(
            AttributeSpec.select_name,
            AttributeSpec.filter_attrs(attrs=attrs, required=RequiredPolicy.REQUIRED)
        )

    @staticmethod
    def optional_attr_names(attrs: 'typing.Iterable[AttributeSpec]'):
        yield from map(
            AttributeSpec.select_name,
            AttributeSpec.filter_attrs(attrs=attrs, required=RequiredPolicy.OPTIONAL)
        )

    @staticmethod
    def deprecated_attr_names(attrs: 'typing.Iterable[AttributeSpec]'):
        return {
            name: value for name, value in map(
                AttributeSpec.select_name_and_default,
                AttributeSpec.filter_attrs(attrs, required=RequiredPolicy.DEPRECATED,
                )
            )
        }

    @staticmethod
    def defaults_dict(attrs: 'typing.Iterable[AttributeSpec]'):
        return {
            name: value for name, value in map(
                AttributeSpec.select_name_and_default,
                AttributeSpec.filter_attrs(attrs, required=RequiredPolicy.OPTIONAL,
                )
            )
        }

    def __init__(
        self,
        name: str,
        doc: str,
        default=None,
        required=None,
        type=str,
    ):
        self._name = ci.util.check_type(name, str)
        self._doc = ci.util.check_type(doc, str)
        self._type = type

        # validate
        if default:
            if required and required not in (RequiredPolicy.OPTIONAL, RequiredPolicy.DEPRECATED):
                raise ValueError()

        self._required_policy = required
        self._default_value = default

    def name(self) -> str:
        return self._name

    def doc(self) -> str:
        return self._doc

    def default_value(self):
        return self._default_value

    def type(self) -> type:
        return self._type

    def required_policy(self):
        return self._required_policy

    def is_required(self):
        if self.required_policy() == RequiredPolicy.REQUIRED:
            return True
        elif self.required_policy() == RequiredPolicy.OPTIONAL:
            return False
        elif self.required_policy() == RequiredPolicy.DEPRECATED:
            return False
        raise NotImplementedError


class Trait(ModelBase):
    def __init__(
        self,
        name: str,
        variant_name: str,
        raw_dict: dict,
        cfg_set=None,
    ):
        self.name = ci.util.not_none(name)
        self.variant_name = ci.util.not_none(variant_name)
        self.cfg_set = cfg_set
        super().__init__(raw_dict=raw_dict)

    @abstractmethod
    def transformer(self):
        raise NotImplementedError

    def __str__(self):
        return 'Trait: {n}'.format(n=self.name)


class TraitTransformer(object):
    name = None # subclasses must overwrite

    def __init__(self):
        ci.util.not_none(self.name)

    def inject_steps(self):
        return []

    @classmethod
    def order_dependencies(cls):
        return set()

    @classmethod
    def dependencies(cls):
        return set()

    @abstractmethod
    def process_pipeline_args(self, pipeline_args: 'JobVariant'): # noqa
        raise NotImplementedError()


class ScriptType(Enum):
    BOURNE_SHELL = 0
    PYTHON3 = 1


def normalise_to_dict(dictish):
    if type(dictish) == str:
        return {dictish: {}}
    if type(dictish) == list:
        values = []
        for v in dictish:
            if type(v) == dict and v:
                values.append(v.popitem())
            elif type(v) != dict:
                values.append((v, {}))
        return dict(values)
    return dictish


def fail(msg):
    raise ModelValidationError(msg)


def select_attr(name):
    return lambda o: o.name
