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

from docutils import nodes
from docutils.parsers.rst import Directive, directives
from docutils.parsers.rst.directives.tables import Table
from sphinx import addnodes
from sphinx.util.nodes import set_source_info, process_index_entry

import sphinxutil
import reflectionutil
import concourse.model.base as base_model

__EXTENSION_VERSION__ = '0.0.1'


def setup(app):
    # called by sphinx to add our 'trait' directive

    app.add_node(TraitNode,
        html=(visit_trait_node, depart_trait_node),
        latex=(visit_trait_node, depart_trait_node),
    )

    app.add_directive('trait', TraitDirective)
    # app.add_role('trait', traitRole)

    return {'version': __EXTENSION_VERSION__}


def visit_trait_node(self, node):
    self.visit_section(node)


def depart_trait_node(self, node):
    self.depart_section(node)


class TraitNode(nodes.section):
    '''
    represents a "trait" documentation in the resulting sphinx document tree
    '''
    pass


class TraitDirective(Directive, sphinxutil.SphinxUtilsMixin):
    required_arguments = 0
    optional_arguments = 1
    has_content = True
    option_spec = {
        'name': directives.unchanged,
    }

    def _init(self, trait_name: str):
        self._node_id = nodes.make_id(nodes.fully_normalize_name('trait-' + trait_name))
        self._node = TraitNode(ids=[self._node_id])
        self._parse_msgs = []
        self._target = nodes.target()
        self.state.add_target(self._node_id, '', self._target, self.lineno)

        ## add node to index
        name_in_index = 'Trait; ' + trait_name
        target_anchor = self._node_id

        self._indexnode = addnodes.index()
        self._indexnode['entries'] = ne = []
        self._indexnode['inline'] = False
        set_source_info(self, self._indexnode)
        ne.extend(process_index_entry(name_in_index, target_anchor))

        self._trait_instance = reflectionutil.trait_instance(trait_name)

    def run(self):
        options = self.options
        trait_name = self.options['name']

        self._init(trait_name=trait_name)

        self.add_title(f'{trait_name} trait')
        self.summary()
        self.attributes()
        self.dependencies()

        return [self._indexnode, self._target, self._node] + self._parse_msgs

    def summary(self):
        if not self.content:
            return

        paragraph = ''
        for line in self.content:
            if line:
                paragraph += line
            else:
                self.add_paragraph(paragraph)
                paragraph = ''
        # emit last line
        self.add_paragraph(paragraph)

    def attributes(self):
        self.add_subtitle('Attributes')

        attr_specs = self._trait_instance._attribute_specs()
        if not attr_specs:
            return self.add_paragraph('This trait has no attributes')

        table_builder = self.create_table_builder()
        table_builder.add_table_header(['name', 'required?', 'default', 'explanation'])

        for attr_spec in attr_specs:
            name = attr_spec.name()
            required = 'yes' if attr_spec.is_required() else 'no'
            default_value = str(attr_spec.default_value())
            doc = attr_spec.doc()

            table_builder.add_table_row((name, required, default_value, doc))

        self.add_table(table_builder)

    def dependencies(self):
        self.add_subtitle('Dependencies')

        trait_deps = self._trait_instance.transformer().dependencies()

        if not trait_deps:
            return self.add_paragraph('This trait has *no* dependencies')

        self.add_paragraph('This trait requires the following traits to be declared:')
        self.add_bullet_list(trait_deps)
