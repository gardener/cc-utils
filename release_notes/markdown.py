import dataclasses
import functools
import logging

import ocm

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
