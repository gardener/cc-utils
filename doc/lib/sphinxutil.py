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


class SphinxUtilsMixin(object):
    '''
    helper methods for creating document elements, such as titles, sections, etc.

    Must be added as a mixin to a class inheriting from `Directive`
    '''
    def add_title(self, text: str):
        self._node += self.create_title(text=text)

    def create_title(self, text: str):
        # title nodes may only be inserted as children of section nodes
        return nodes.title(text=text)

    def add_subtitle(self, text: str):
        self._node += self.create_subtitle(text=text)

    def create_subtitle(self, text: str):
        # seems to be no difference between subtitle and title in html
        subtitle_node = nodes.subtitle(text=text)
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
        title_node = self._get_subtitle_node(title)
        section_node += title_node
        section_node += par_node
        return section_node, messages

    def create_bullet_list(self, lines: [str]):
        bullet_list = nodes.bullet_list()
        parse_msgs = []
        for line in lines:
            text_nodes, messages = self.state.inline_text(line, self.lineno + self.content_offset)
            parse_msgs += messages
            line_node = nodes.line('', *text_nodes)
            list_item = nodes.list_item()
            list_item += line_node
            bullet_list += list_item

        return bullet_list, parse_msgs
