# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import dataclasses
import enum
import os
import sys
import textwrap
import types
import typing
import yaml


import ci.util
import concourse.model.base as base_model


# add repository root to pythonpath
sys.path.append(os.path.abspath('../..'))


class SafeEnumDumper(yaml.SafeDumper):
    def represent_data(self, data):
        if isinstance(data, enum.Enum):
            return super().represent_data(data.value)
        return super().represent_data(data)


def _is_generic_alias(value, /):
    return isinstance(value, (typing._GenericAlias, types.GenericAlias))


class AttributesDocumentation:
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
        if _is_generic_alias(type_):
            if type_.__origin__ is dict:
                key_type, val_type = type_.__args__
                return f'Dict[{self._type_name(key_type)}, {self._type_name(val_type)}]'
            elif type_.__origin__ in (list, set, tuple):
                # yaml only supports list, so it is okay to always use `List` for documentation
                return f'List[{self._type_name(type_.__args__[0])}]'
            elif type_.__origin__ is typing.Union:
                type_str = "One of: \n \n"
                type_str += '\n'.join([f'- {self._type_name(a)}' for a in type_.__args__])
                return type_str
            else:
                raise NotImplementedError
        elif isinstance(type_, types.UnionType):
            return str(type_)
        else:
            return type_.__name__

    def _default_value(self, default_value):
        if callable(default_value):
            return default_value.__name__

        elif dataclasses.is_dataclass(default_value):
            as_yaml = yaml.dump(dataclasses.asdict(default_value), Dumper=SafeEnumDumper)
            return (
                '.. code-block:: yaml\n\n'
                f'{textwrap.indent(as_yaml, "  ")}'
            )

        elif isinstance(default_value, enum.Enum):
            return f'`{default_value.value}`'

        elif isinstance(default_value, str):
            return default_value

        elif isinstance(default_value, (dict, list)):
            if isinstance(default_value, list) and default_value \
                and dataclasses.is_dataclass(default_value[0]):
                default_value = [
                    dataclasses.asdict(e, dict_factory=ci.util.dict_factory_enum_serialisiation)
                    for e in default_value
                ]
            return (
                '.. code-block:: yaml\n\n'
                f'{textwrap.indent(yaml.dump(default_value, Dumper=SafeEnumDumper), "  ")}'
            )

        # fallback
        return str(default_value)

    def _attr_spec_to_table_row(self, attr_spec, prefix=None):
        name = attr_spec.name()
        required = 'yes' if attr_spec.is_required() else 'no'

        default_value = self._default_value(attr_spec.default_value())

        doc = textwrap.dedent(attr_spec.doc())

        type_ = attr_spec.type()
        type_str = self._type_name(type_)
        if _is_generic_alias(type_):
            if type_.__origin__ is dict:
                # assumption: type is typing.Dict[T1, T2]
                _, val_type = type_.__args__
                if not issubclass(val_type, (str, int, bool, float)):
                    self.add_child(
                        model_element_type=val_type,
                        element_name=f'{name}.<user-chosen>'
                    )
            elif type_.__origin__ in (list, set, tuple):
                # Also check type to support list of enum values
                if (
                    issubclass(type_.__args__[0], base_model.AttribSpecMixin)
                    or issubclass(type_.__args__[0], enum.Enum)
                ):
                    self.add_child(model_element_type=type_.__args__[0], element_name=name)
        elif isinstance(type_, types.UnionType):
            pass # no special handling for union-types (yet)
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
            raise NotImplementedError(f'{self.__dict__}:{self._model_element_type}')

        return table_builder
