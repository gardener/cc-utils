import dataclasses
import logging
import typing
from collections import defaultdict

import release_notes.model as rnm

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Title:
    display: str
    identifiers: list[str]

    def __hash__(self):
        return hash(self.display)

    def __eq__(self, other):
        return (
            other is not None
            and isinstance(other, Title)
            and (other == self or hash(other) == hash(self))
        )


def _simple_title(display: str) -> Title:
    return Title(display, [display.lower()])


categories = [
    Title('⚠️ Breaking Changes', ['action', 'breaking']),
    Title('📰 Noteworthy', ['noteworthy']),
    Title('🏃 Others', ['improvement', 'other']),
    Title('✨ New Features', ['feature']),
    Title('🐛 Bug Fixes', ['bugfix', 'fix']),
    Title('📖 Documentation', ['doc']),
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

        for cat, notes in cats.items():
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
                    lines = note.source_block.note_message.splitlines()
                    objs.append(list_item_header_from_notes(lines[0], note, cat, group))
                    if len(lines) > 1:
                        objs.extend(list_item_from_lines(lines[1:]))
    return objs
