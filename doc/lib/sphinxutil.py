# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import typing

from docutils import nodes
from docutils.statemachine import ViewList
from sphinx.util.docutils import switch_source_input


class SphinxUtilsMixin:
    '''
    helper methods for creating document elements, such as titles, sections, etc.

    Must be added as a mixin to a class inheriting from `Directive`
    '''
    def add_title(self, text: str):
        title_node = self.create_title(text=text)
        self._node += title_node

    def create_title(self, text: str):
        # title nodes may only be inserted as children of section nodes
        return nodes.title(text=text)

    def add_subtitle(self, text: str):
        subtitle_node = self.create_subtitle(text=text)
        self._node += subtitle_node

    def create_subtitle(self, text: str):
        # seems to be no difference between subtitle and title in html
        text_nodes, messages = self.state.inline_text(text, self.lineno)
        subtitle_node = nodes.title(text, '', *text_nodes)
        self._parse_msgs += messages
        return subtitle_node

    def add_paragraph(self, contents: str):
        paragraph_node, messages = self.create_paragraph(contents=contents)
        self._node += paragraph_node
        self._parse_msgs += messages

    def create_paragraph(self, contents: str):
        # Parse text.
        text_nodes, messages = self.state.inline_text(contents, self.lineno + self.content_offset)
        paragraph_node = nodes.paragraph('', *text_nodes)
        return paragraph_node, messages

    def create_section(self, title, content, parent_ids):
        ids = parent_ids + '-' + nodes.fully_normalize_name(title)
        section_node = nodes.section(ids=[ids])

        par_node, messages = self.create_paragraph(content)
        title_node = self.create_subtitle(title)
        section_node += title_node
        section_node += par_node
        return section_node, messages

    def add_bullet_list(self, lines: typing.Iterable[str]):
        list_node, messages = self.create_bullet_list(lines=lines)
        self._node += list_node
        self._parse_msgs += messages

    def create_bullet_list(self, lines: typing.Iterable[str]):
        bullet_list = nodes.bullet_list()
        parse_msgs = []
        for line in lines:
            text_nodes, messages = self.state.inline_text(line, self.lineno + self.content_offset)
            parse_msgs += messages
            par_node = nodes.paragraph('', '', *text_nodes)
            list_item = nodes.list_item('', par_node)
            bullet_list += list_item

        return bullet_list, parse_msgs

    def create_table_builder(self, table_classes:typing.List[str] = ['colwidths-auto']):
        '''Helper method to obtain an instance of TableBuilder for the given table_classes
        '''
        return TableBuilder(self.state, self.state_machine, table_classes)

    def add_table(self, table:'TableBuilder'):
        table_node = table.create_table()
        self._node += table_node


class TableBuilder:
    def __init__(
        self,
        state,
        state_machine,
        table_classes: typing.List[str]=['colwidths-auto'],
    ):
        self.table_node = nodes.table('', classes=table_classes)

        # state and state_machine are required by the _create_row method taken from sphinx
        self.state_machine = state_machine
        self.state = state

        self.head = nodes.thead('')
        self.body = nodes.tbody('')
        self.groups = None

    # taken and adjusted from sphinx.ext.Autosummary.get_table()
    def _create_row(self, *column_texts):
        row = nodes.row('')
        source, line = self.state_machine.get_source_and_line()
        for text_line in column_texts:
            node = nodes.paragraph('')
            vl = ViewList()
            if text_line is None:
                continue
            for text in text_line.split('\n'):
                vl.append(text, '%s:%d' % (source, line))
            with switch_source_input(self.state, vl):
                self.state.nested_parse(vl, 0, node)
                try:
                    if isinstance(node[0], nodes.paragraph) and len(node.children) == 1:
                        node = node[0]
                except IndexError:
                    pass
                row.append(nodes.entry('', node))
        return row

    def _setup_column_groups(self, column_count: int):
        self.column_count = column_count
        self.group = nodes.tgroup('', cols=column_count)
        for _ in range(column_count):
            self.group.append(nodes.colspec(''))

    def add_table_header(self, row_content: typing.List[str]):
        if self.groups is None:
            self._setup_column_groups(column_count=len(row_content))
        else:
            raise ValueError('A table may only have one table head which must be added first.')
        self.head.append(self._create_row(*row_content))
        return self

    def add_table_row(self, row_content: typing.List[str]):
        if self.groups is None:
            self._setup_column_groups(column_count=len(row_content))

        # adding rows with less than column_count columns automatically inserts empty columns
        if len(row_content) > self.column_count:
            raise ValueError('Can only add rows with at most {c} columns to this table.'.format(
                c=self.column_count
            ))

        self.body.append(self._create_row(*row_content))
        return self

    def create_table(self):
        if len(self.head.children) > 0:
            self.group.append(self.head)

        if len(self.body.children) > 0:
            self.group.append(self.body)

        self.table_node.append(self.group)
        return self.table_node
