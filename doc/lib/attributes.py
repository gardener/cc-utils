# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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
        if isinstance(type_, typing._GenericAlias):
            if type_.__origin__ is dict:
                # assumption: type is typing.Dict[T1, T2]
                key_type, val_type = type_.__args__
                self.add_child(
                    model_element_type=val_type,
                    element_name=f'{name}.<user-chosen>'
                )
                type_str = type_._name + f'[{str(key_type)}, {str(val_type)}]'
            elif type_.__origin__ in (list, set):
                type_str = type_._name + f'[{str(type_.__args__[0])}]'
                # Also check type to support list of enum values
                if (
                    issubclass(type_.__args__[0], base_model.AttribSpecMixin)
                    or issubclass(type_.__args__[0], enum.Enum)
                ):
                    self.add_child(model_element_type=type_.__args__[0], element_name=name)

            else:
                type_str = type_.__name__
        elif (
            issubclass(type_, base_model.AttribSpecMixin)
            or issubclass(type_, enum.Enum)
        ):
            # recurse to child element
            self.add_child(model_element_type=type_, element_name=name)
            type_str = type_.__name__
        else:
            type_str = type_.__name__

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
