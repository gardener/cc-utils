# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import sphinxutil
import attributes
import enum

from concourse.model.base import EnumWithDocumentation


class AttributesDocMixin(sphinxutil.SphinxUtilsMixin):
    def attributes(self, model_element_type):
        attributes_doc = attributes.AttributesDocumentation(
            model_element_type,
            prefix='',
        )

        def render_element_attribs(prefix: str, attributes_doc):
            if prefix:
                subtitle = f'{prefix} *({attributes_doc._model_element_type.__name__})* Attributes'
            else:
                subtitle = 'Attributes'
            if issubclass(attributes_doc._model_element_type, enum.Enum):
                subtitle = f'{prefix} Enumeration Values'

            self.add_subtitle(subtitle)

            if (
                issubclass(attributes_doc._model_element_type, enum.Enum)
                and not issubclass(attributes_doc._model_element_type, EnumWithDocumentation)
            ):
                self.add_bullet_list([f'``{e.value}``' for e in attributes_doc._model_element_type])
            else:
                table_builder = self.create_table_builder()
                attributes_doc.fill_table(table_builder)
                self.add_table(table_builder)

            for child_element in attributes_doc.children():
                render_element_attribs(child_element._prefix, child_element)

        render_element_attribs('', attributes_doc)
