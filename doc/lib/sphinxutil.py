from docutils import nodes


class SphinxUtilsMixin(object):
    '''
    helper methods for creating document elements, such as titles, sections, etc.

    Must be added as a mixin to a class inheriting from `Directive`
    '''
    def create_title(self, text: str):
        # title nodes may only be inserted as children of section nodes
        return nodes.title(text=text)

    def create_subtitle(self, text: str):
        # seems to be no difference between subtitle and title in html
        subtitle_node = nodes.subtitle(text=text)
        return subtitle_node

    def create_paragraph(self, content: str):
        # Parse text.
        text_nodes, messages = self.state.inline_text(content, self.lineno + self.content_offset)
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

