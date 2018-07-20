# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from model.base import ModelValidationError

def not_none(value):
    if value is None:
        raise ValueError('must not be none')
    return value
# export shorter alias
not_none = not_none

class ModelBase(object):
    def __init__(self, raw_dict: dict):
        not_none(raw_dict)
        self.custom_init(raw_dict)
        self.raw = raw_dict

    def validate(self):
        pass

    def custom_init(self, raw_dict: dict):
        pass


class Trait(object): # todo: base on NamedModelBase
    def __init__(self, name: str, variant_name: str, raw_dict: dict):
        self.name = not_none(name)
        self.variant_name = not_none(variant_name)
        self.raw = not_none(raw_dict)

    @abstractmethod
    def transformer(self):
        raise NotImplementedError()

    def __str__(self):
        return 'Trait: {n}'.format(n=self.name)


class TraitTransformer(object):
    def __init__(self, name: str):
        self.name = not_none(name)

    def inject_steps(self):
        return []

    def order_dependencies(self):
        return {self.name}

    def dependencies(self):
        return {self.name}

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

