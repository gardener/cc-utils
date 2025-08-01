import dataclasses
import functools
import logging
import typing
from collections import defaultdict

import ocm
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


def list_item_from_entry(
    entry: rnm.ReleaseNoteEntry,
    audience: rnm.ReleaseNotesAudience
) -> ListItem:
    # Replace newlines with proper Markdown line breaks
    message = entry.contents.replace('\n', '  \n  ')
    author_str = f'@{entry.author.username}' if entry.author else ''
    reference_str = entry.pullrequest or ''

    return ListItem(
        level=1,
        text=f'`[{audience.value}]` {message} by {author_str} {reference_str}'
    )


def render(notes: set[rnm.ReleaseNotesDoc]):
    objs = []

    # Order by component
    components: dict[str, list[rnm.ReleaseNotesDoc]] = defaultdict(list)
    for note in notes:
        comp_name = note.ocm.component_name
        components[comp_name].append(note)

    for component, comp_notes in components.items():
        objs.append(Header(level=1, title=f'[{component}]'))

        # Group by category
        categories: dict[rnm.ReleaseNotesCategory, list[rnm.ReleaseNoteEntry]] = defaultdict(list)
        for note in comp_notes:
            for entry in note.release_notes:
                if entry.category:
                    categories[entry.category].append(entry)

        sorted_categories = sorted(
            categories.items(),
            key=lambda t: rnm.ReleaseNotesCategory.category_priority(t[0])
        )

        for category, entries in sorted_categories:
            objs.append(Header(level=2, title=rnm.ReleaseNotesCategory.category_title(category)))

            # Group by audience
            audiences: dict[rnm.ReleaseNotesAudience, list[rnm.ReleaseNoteEntry]] = defaultdict(list)
            for entry in entries:
                audience = entry.audience or rnm.ReleaseNotesAudience.USER
                audiences[audience].append(entry)

            for audience, audience_entries in audiences.items():
                for entry in audience_entries:
                    objs.append(list_item_from_entry(
                        entry=entry,
                        audience=audience,
                    ))

    return objs


def release_notes_for_ocm_resource(resource: ocm.Resource) -> str | None:
    if not resource.access:
        return None

    if resource.access.type is ocm.AccessType.OCI_REGISTRY:
        return f'- {resource.name}: `{resource.access.imageReference}`'

    return None


def release_note_for_ocm_component(component: ocm.Component) -> str | None:
    '''Create a markdown string containing information about the Resources included in the given
    Component.
    '''
    local_resources = [
        r
        for r in component.resources
        if r.relation is ocm.ResourceRelation.LOCAL
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

        if resource_type is ocm.ArtefactType.OCI_IMAGE:
            category_title = 'Container (OCI) Images'
        elif resource_type is ocm.ArtefactType.HELM_CHART:
            category_title = 'Helm Charts'
        else:
            category_title = str(resource_type)

        if resource_lines:
            category_markdown = (
                '## ' + category_title + '\n' + '\n'.join(sorted(resource_lines)) + '\n'
            )
            component_release_notes += category_markdown

    return component_release_notes
