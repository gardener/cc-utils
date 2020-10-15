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

import os
import sys
import textwrap
import typing
import enum


import ci.util
import concourse.model.base as base_model


# add repository root to pythonpath
sys.path.append(os.path.abspath('../..'))


class AttributesDocumentation(object):
    def __init__(
        self,
        model_element_type,
        prefix: str='',
    ):
        self._model_element_type = model_element_type
        self._child_elements = []
        self._prefix = ci.util.check_type(prefix, str)

    def add_child(
        self,
        model_element_type,
        element_name: str,
    ):
        if self._prefix:
            child_prefix = '.'.join((self._prefix, element_name))
        else:
            child_prefix = element_name

        child_documentation = AttributesDocumentation(
            model_element_type,
            prefix=child_prefix,
        )

        self._child_elements.append(child_documentation)
        return child_documentation

    def children(self):
        return self._child_elements

    def _type_name(self, type_):
        if isinstance(type_, typing._GenericAlias):
            if type_.__origin__ is dict:
                key_type, val_type = type_.__args__
                return type_._name + f'[{self._type_name(key_type)}, {self._type_name(val_type)}]'
            elif type_.__origin__ in (list, set):
                return type_._name + f'[{self._type_name(type_.__args__[0])}]'
            elif type_.__origin__ is typing.Union:
                type_str = "One of: \n \n"
                type_str += '\n'.join([f'- {self._type_name(a)}' for a in type_.__args__])
                return type_str
            else:
                raise NotImplementedError
        else:
            return type_.__name__

    def _attr_spec_to_table_row(self, attr_spec, prefix=None):
        name = attr_spec.name()
        required = 'yes' if attr_spec.is_required() else 'no'

        default_value = attr_spec.default_value()
        if callable(default_value):
            default_value = default_value.__name__
        else:
            default_value = str(default_value)

        doc = textwrap.dedent(attr_spec.doc())

        type_ = attr_spec.type()
        type_str = self._type_name(type_)
        if isinstance(type_, typing._GenericAlias):
            if type_.__origin__ is dict:
                # assumption: type is typing.Dict[T1, T2]
                _, val_type = type_.__args__
                self.add_child(
                    model_element_type=val_type,
                    element_name=f'{name}.<user-chosen>'
                )
            elif type_.__origin__ in (list, set):
                # Also check type to support list of enum values
                if (
                    issubclass(type_.__args__[0], base_model.AttribSpecMixin)
                    or issubclass(type_.__args__[0], enum.Enum)
                ):
                    self.add_child(model_element_type=type_.__args__[0], element_name=name)
        elif (
            issubclass(type_, base_model.AttribSpecMixin)
            or issubclass(type_, enum.Enum)
        ):
            # recurse to child element
            self.add_child(model_element_type=type_, element_name=name)

        if issubclass(self._model_element_type, enum.Enum):
            return (name, doc)
        else:
            return (name, required, default_value, type_str, doc)

    def fill_table(self, table_builder):

        if issubclass(self._model_element_type, base_model.EnumWithDocumentation):
            table_builder.add_table_header(['value', 'explanation'])
            for e in self._model_element_type:
                table_builder.add_table_row((e.value, e.__doc__))

        elif issubclass(self._model_element_type, base_model.AttribSpecMixin):

            table_builder.add_table_header(
                ['name', 'required?', 'default', 'type', 'explanation']
            )
            for attr_spec in self._model_element_type._attribute_specs():
                table_builder.add_table_row(self._attr_spec_to_table_row(attr_spec))
        else:
            raise NotImplementedError

        return table_builder
