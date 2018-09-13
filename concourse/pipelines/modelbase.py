# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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


from abc import abstractmethod
from enum import Enum

import util
from model.base import ModelValidationError


def not_none(value):
    if value is None:
        raise ValueError('must not be none')
    return value


class ModelDefaultsMixin(object):
    def _defaults_dict(self):
        return {}

    def _apply_defaults(self, raw_dict):
        self.raw = util.merge_dicts(
            self._defaults_dict(),
            raw_dict,
        )

    def _defaults_dict(self):
        return {}


class ModelValidationMixin(object):
    def _required_attributes(self):
        return ()

    def _optional_attributes(self):
        return ()

    def _known_attributes(self):
        return set(self._required_attributes()) | \
                set(self._optional_attributes()) | \
                set(self._defaults_dict().keys())

    def validate(self):
        self._validate_required_attributes()
        self._validate_known_attributes()

    def _validate_required_attributes(self):
        missing_attributes = [a for a in self._required_attributes() if a not in self.raw]
        if missing_attributes:
            raise ModelValidationError(
                'the following required attributes are absent: {m}'.format(
                    m=', '.join(missing_attributes),
                )
            )

    def _validate_known_attributes(self):
        unknown_attributes = [a for a in self.raw if a not in self._known_attributes()]
        if unknown_attributes:
            raise ModelValidationError(
                '{e}: the following attributes are unknown: {m}'.format(
                    e=str(self),
                    m=', '.join(unknown_attributes)
                )
            )


class ModelBase(ModelDefaultsMixin, ModelValidationMixin):
    def __init__(self, raw_dict: dict):
        not_none(raw_dict)

        self._apply_defaults(raw_dict=raw_dict)
        self.custom_init(self.raw)

    def custom_init(self, raw_dict: dict):
        pass

    def _children(self):
        return ()


class Trait(ModelBase):
    def __init__(self, name: str, variant_name: str, raw_dict: dict):
        self.name = not_none(name)
        self.variant_name = not_none(variant_name)
        super().__init__(raw_dict=raw_dict)

    @abstractmethod
    def transformer(self):
        raise NotImplementedError()

    def __str__(self):
        return 'Trait: {n}'.format(n=self.name)


class TraitTransformer(object):
    name = None # subclasses must overwrite

    def __init__(self):
        not_none(self.name)

    def inject_steps(self):
        return []

    @classmethod
    def order_dependencies(cls):
        return set()

    @classmethod
    def dependencies(cls):
        return set()

    @abstractmethod
    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
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
            if type(v) == dict:
                values.append(v.popitem())
            else:
                values.append((v, {}))
        return dict(values)
    return dictish


def fail(msg):
    raise ModelValidationError(msg)


def select_attr(name):
    return lambda o: o.name
