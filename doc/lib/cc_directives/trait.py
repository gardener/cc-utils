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


def trait_node_id_from_trait_name(trait_name:str):
    return nodes.make_id(nodes.fully_normalize_name(f'trait-{trait_name}'))


class TraitNode(nodes.section):
    '''
    represents a "trait" documentation in the resulting sphinx document tree
    '''
    pass


class TraitDirective(Directive, cc_directives.base.AttributesDocMixin, sphinxutil.SphinxUtilsMixin):
    required_arguments = 0
    optional_arguments = 1
    has_content = True
    option_spec = {
        'name': directives.unchanged,
    }

    def _init(self, trait_name: str):
        self._node_id = trait_node_id_from_trait_name(trait_name)
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
        self._trait_class = reflectionutil.trait_class(trait_name)

    def run(self):
        trait_name = self.options['name']

        self._init(trait_name=trait_name)

        self.summary()
        self.attributes(self._trait_class)
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

    def dependencies(self):
        self.add_subtitle('Dependencies')

        trait_deps = self._trait_instance.transformer().dependencies()

        if not trait_deps:
            return self.add_paragraph('This trait has *no* dependencies')

        # transform dependency names to sphinx-refs and let the parser handle them
        # when parsing the list-entries
        ref_list = list()
        for dependency_name in trait_deps:
            link_target = trait_node_id_from_trait_name(dependency_name)
            link_text = f'{dependency_name} trait'
            ref_list.append(f':ref:`{link_text} <{link_target}>`')

        self.add_paragraph('This trait requires the following traits to be declared:')
        self.add_bullet_list(ref_list)
