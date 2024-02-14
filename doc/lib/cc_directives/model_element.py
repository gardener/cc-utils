# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from docutils import nodes
from docutils.parsers.rst import Directive, directives
from sphinx import addnodes
from sphinx.util.nodes import set_source_info, process_index_entry

import sphinxutil
import reflectionutil
import cc_directives.base

__EXTENSION_VERSION__ = '0.0.1'


def setup(app):
    # called by sphinx to add our directive

    app.add_node(ModelElementNode,
        html=(visit_trait_node, depart_trait_node),
        latex=(visit_trait_node, depart_trait_node),
    )

    app.add_directive('model_element', ModelElementDirective)

    return {'version': __EXTENSION_VERSION__}


def visit_trait_node(self, node):
    self.visit_section(node)


def depart_trait_node(self, node):
    self.depart_section(node)


class ModelElementNode(nodes.section):
    '''
    represents a "pipeline_step" documentation in the resulting sphinx document tree
    '''
    pass


class ModelElementDirective(
    Directive,
    cc_directives.base.AttributesDocMixin,
    sphinxutil.SphinxUtilsMixin,
):
    required_arguments = 0
    optional_arguments = 0
    has_content = True
    option_spec = {
        'name': directives.unchanged,
        'qualified_type_name': directives.unchanged,
    }

    def _init(self, name: str, qualified_type_name: str):
        self._node_id = nodes.make_id(nodes.fully_normalize_name(name))
        self._node = ModelElementNode(ids=[self._node_id])
        self._parse_msgs = []
        self._target = nodes.target()
        self.state.add_target(self._node_id, '', self._target, self.lineno)

        ## add node to index
        name_in_index = 'ModelElement; ' + name
        target_anchor = self._node_id

        self._indexnode = addnodes.index()
        self._indexnode['entries'] = ne = []
        self._indexnode['inline'] = False
        set_source_info(self, self._indexnode)
        ne.extend(process_index_entry(name_in_index, target_anchor))

        self._model_element_type = reflectionutil.model_element_type(
            qualified_type_name=qualified_type_name,
        )

    def run(self):
        name = self.options['name']
        qualified_type_name = self.options['qualified_type_name']

        self._init(name=name, qualified_type_name=qualified_type_name)

        self.attributes(self._model_element_type)

        return [self._indexnode, self._target, self._node] + self._parse_msgs
