import dataclasses
import functools
import logging
import typing
from collections import defaultdict

import gci.componentmodel as cm
import release_notes.model as rnm

logger = logging.getLogger(__name__)


@functools.total_ordering
@dataclasses.dataclass
class Title:
    display: str
    identifiers: list[str]
    priority: int

    def __hash__(self):
        return hash(self.display)

    def __eq__(self, other):
        return (
            other is not None
            and isinstance(other, Title)
            and (other == self or hash(other) == hash(self))
        )

    def __lt__(self, other):
        if not isinstance(other, Title):
            raise ValueError(other)
        return self.priority < other.priority


def _simple_title(display: str) -> Title:
    return Title(display=display, identifiers=[display.lower()], priority=0)


categories = [
    Title(display='⚠️ Breaking Changes', identifiers=['action', 'breaking'], priority=0),
    Title(display='📰 Noteworthy', identifiers=['noteworthy'], priority=1),
    Title(display='✨ New Features', identifiers=['feature'], priority=2),
    Title(display='🐛 Bug Fixes', identifiers=['bugfix', 'fix'], priority=3),
    Title(display='🏃 Others', identifiers=['improvement', 'other'], priority=4),
    Title(display='📖 Documentation', identifiers=['doc'], priority=5),
]

target_groups = [
    _simple_title('USER'),
    _simple_title('OPERATOR'),
    _simple_title('DEVELOPER'),
    _simple_title('DEPENDENCY'),
]

# identifier -> Title
categories_by_identifier: dict[str, Title] = {i: k for k in categories for i in k.identifiers}
target_groups_by_identifier: dict[str, Title] = {i: k for k in target_groups for i in k.identifiers}


@dataclasses.dataclass
class Header:
    level: int
    title: str

    def __str__(self):
        return f"{'#' * self.level} {self.title}\n"  # there should be a new line after the header


@dataclasses.dataclass
class ListItem:
    level: int
    text: str

    def __str__(self):
        return f"{'  ' * (self.level - 1)}- {self.text}"


def list_item_from_lines(lines: list[str], start_level: int = 1) -> list[ListItem]:
    objs = []

    level = start_level
    last_index: typing.Optional[int] = None
    for idx, line in enumerate(lines):
        if line.startswith(' ') or line.startswith('\t'):
            first_index = line.index(line.lstrip(' \t')[0])
            if last_index is None or last_index < first_index:
                last_index = first_index
                if idx == 0:
                    level += 1
            elif last_index > first_index:
                level -= 1
        else:
            level = start_level
        objs.append(ListItem(level=level, text=line.lstrip(' \t-')))
    return objs


def get_repo_name(full_name: str) -> str:
    return full_name[full_name.index('/') + 1:]


def get_reference_for_note(note: rnm.ReleaseNote) -> str:
    if note.is_current_repo:
        return note.reference_str
    return get_repo_name(
        note.source_component.name
    ) + note.reference.type.prefix + note.reference.identifier


def list_item_header_from_notes(
        line: str,
        note: rnm.ReleaseNote,
        category: Title,
        group: Title,
    ) -> ListItem:
    return ListItem(
        level=1,
        text=(
            f'`[{group.display}]` {line} by '
            f'{note.source_block.author or note.author} [{get_reference_for_note(note)}]'
        ),
    )


def list_item_from_note(
        message: str,
        note: rnm.ReleaseNote,
        group: Title,
) -> ListItem:
    # Replace newlines with two spaces _followed by_ newlines, as this is the proper way to do
    # a line-break in a list-item.
    # Also indent the next line, of course.
    message = message.replace('\n', '  \n  ')
    return ListItem(
        level=1,
        text=(
            f'`[{group.display}]` {message} by '
            f'{note.source_block.author or note.author} [{get_reference_for_note(note)}]'
        ),
    )


def render(notes: set[rnm.ReleaseNote]):
    objs = []

    # order by component
    components: dict[str, list[rnm.ReleaseNote]] = defaultdict(list)
    for note in notes:
        components[note.source_component.name].append(note)

    for component, notes in components.items():
        objs.append(Header(level=1, title=f'[{get_repo_name(component)}]'))

        # group by category
        cats: dict[Title, list[rnm.ReleaseNote]] = defaultdict(list)
        for note in notes:
            cat_title = categories_by_identifier.get(note.source_block.category)
            if not cat_title:
                logger.info(f"cannot find category '{note.source_block.category}'")
                continue
            cats[cat_title].append(note)

        # sort by category-priority, ascending. This will keep the order stable.
        for cat, notes in sorted(cats.items(), key=lambda tuple: tuple[0]):
            objs.append(Header(level=2, title=cat.display))

            # group by target group
            groups: dict[Title, list[rnm.ReleaseNote]] = defaultdict(list)
            for note in notes:
                group_title = target_groups_by_identifier.get(note.source_block.target_group)
                if not group_title:
                    logger.info(f"cannot find target group '{note.source_block.target_group}'")
                    continue
                groups[group_title].append(note)

            for group, notes in groups.items():
                for note in notes:
                    message = note.source_block.note_message
                    objs.append(list_item_from_note(message, note, group))
    return objs


def release_notes_for_ocm_resource(resource: cm.Resource) -> str | None:

    if resource.type == cm.ArtefactType.OCI_IMAGE:
        if resource.access.type == cm.AccessType.OCI_REGISTRY:
            return f'- {resource.name}: `{resource.access.imageReference}`'

    return None


def release_note_for_ocm_component(component: cm.Component) -> str | None:
    '''Create a markdown string containing information about the Resources included in the given
    Component.
    '''
    local_resources = [
        r
        for r in component.resources
        if r.relation is cm.ResourceRelation.LOCAL
    ]

    component_release_notes = ''
    for resource_type in sorted({resource.type for resource in local_resources}):
        matching_resources = [r for r in local_resources if r.type == resource_type]
        resource_lines = {
            l for l in (
                release_notes_for_ocm_resource(r)
                for r in matching_resources
            ) if l is not None
        }

        if resource_type == cm.ResourceType.OCI_IMAGE:
            category_title = 'Docker Images'
        else:
            category_title = str(resource_type)

        if resource_lines:
            category_markdown = (
                '## ' + category_title + '\n' + '\n'.join(sorted(resource_lines)) + '\n'
            )
            component_release_notes += category_markdown

    return component_release_notes
