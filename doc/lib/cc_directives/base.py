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

import sphinxutil
import attributes
import enum


class AttributesDocMixin(sphinxutil.SphinxUtilsMixin):
    def attributes(self, model_element_type):
        attributes_doc = attributes.AttributesDocumentation(
            model_element_type,
            prefix='',
        )

        def render_element_attribs(prefix: str, attributes_doc):
            if prefix:
                subtitle = f'{prefix} Attributes'
            else:
                subtitle = 'Attributes'
            if issubclass(attributes_doc._model_element_type, enum.Enum):
                subtitle = f'{prefix} Enumeration Values'

            self.add_subtitle(subtitle)

            table_builder = self.create_table_builder()
            attributes_doc.fill_table(table_builder)
            self.add_table(table_builder)

            for child_element in attributes_doc.children():
                render_element_attribs(child_element._prefix, child_element)

        render_element_attribs('', attributes_doc)
