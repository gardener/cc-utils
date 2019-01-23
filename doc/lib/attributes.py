# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

import os
import sys
import textwrap


import concourse.model.base as model_base
import model
import sphinxutil
import util


# add repository root to pythonpath
sys.path.append(os.path.abspath('../..'))


class AttributesDocumentation(object):
    def __init__(
        self,
        model_element: model_base.AttribSpecMixin,
        prefix: str='',
    ):
        self._model_element = util.check_type(model_element, model_base.AttribSpecMixin)
        self._child_elements = []
        self._prefix = util.check_type(prefix, str)

    def add_child(
        self,
        model_element: model_base.AttribSpecMixin,
        element_name: str,
    ):
        if self._prefix:
            child_prefix = '.'.join(self.prefix, element_name)
        else:
            child_prefix = element_name

        child_documentation = AttributesDocumentation(
            model_element,
            prefix=child_prefix,
        )

        self._child_elements.append(child_documentation)
        return child_documentation

    def children(self):
        return self._child_elements

    def fill_table(self, table_builder):
        table_builder.add_table_header(['name', 'required?', 'default', 'type', 'explanation'])

        def attr_to_table_row(attr_spec, prefix=None):
            name = attr_spec.name()
            required = 'yes' if attr_spec.is_required() else 'no'
            default_value = str(attr_spec.default_value())
            doc = textwrap.dedent(attr_spec.doc())

            type_ = attr_spec.type()
            if issubclass(type_, model_base.AttribSpecMixin):
                type_str = type_.__name__
                # recurse to child element
                if issubclass(type_, model.NamedModelElement):
                    child_element = type_(name='<user-chosen>', raw_dict={})
                else:
                    child_element = type_(raw_dict={})

                self.add_child(model_element=child_element, element_name=name)

            else:
                type_str = type_.__name__

            table_builder.add_table_row((name, required, default_value, type_str, doc))

        for attr_spec in self._model_element._attribute_specs():
            attr_to_table_row(attr_spec)

        return table_builder
