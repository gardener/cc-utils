import collections
import dataclasses
import enum
import functools
import logging
import re
import typing
import uuid

import git
import github3.pulls

import ocm as ocm_model


logger = logging.getLogger(__name__)


RELEASE_NOTES_DOC_SUFFIX = '.release-notes.yaml'


class ReleaseNotesType(enum.StrEnum):
    STANDARD = 'standard'
    PRERENDERED = 'prerendered'


class ReleaseNotesCategory(enum.StrEnum):
    ACTION = 'action'
    BREAKING = 'breaking'
    BUGFIX = 'bugfix'
    DOCUMENTATION = 'doc'
    FEATURE = 'feature'
    FIX = 'fix'
    IMPROVEMENT = 'improvement'
    NOTEWORTHY = 'noteworthy'
    OTHER = 'other'

    @staticmethod
    def category_title(category: typing.Self) -> str:
        return {
            ReleaseNotesCategory.ACTION: '⚠️ Breaking Changes',
            ReleaseNotesCategory.BREAKING: '⚠️ Breaking Changes',
            ReleaseNotesCategory.BUGFIX: '🐛 Bug Fixes',
            ReleaseNotesCategory.DOCUMENTATION: '📖 Documentation',
            ReleaseNotesCategory.FEATURE: '✨ New Features',
            ReleaseNotesCategory.FIX: '🐛 Bug Fixes',
            ReleaseNotesCategory.IMPROVEMENT: '🏃 Others',
            ReleaseNotesCategory.NOTEWORTHY: '📰 Noteworthy',
            ReleaseNotesCategory.OTHER: '🏃 Others',
        }[category]

    @staticmethod
    def category_priority(category: typing.Self) -> int:
        return {
            ReleaseNotesCategory.ACTION: 0,
            ReleaseNotesCategory.BREAKING: 0,
            ReleaseNotesCategory.NOTEWORTHY: 1,
            ReleaseNotesCategory.FEATURE: 2,
            ReleaseNotesCategory.BUGFIX: 3,
            ReleaseNotesCategory.FIX: 3,
            ReleaseNotesCategory.IMPROVEMENT: 4,
            ReleaseNotesCategory.OTHER: 4,
            ReleaseNotesCategory.DOCUMENTATION: 5,
        }[category]


class ReleaseNotesAudience(enum.StrEnum):
    DEPENDENCY = 'dependency'
    DEVELOPER = 'developer'
    OPERATOR = 'operator'
    USER = 'user'

    @staticmethod
    def audience_priority(audience: typing.Self) -> int:
        return {
            ReleaseNotesAudience.OPERATOR: 0,
            ReleaseNotesAudience.USER: 1,
            ReleaseNotesAudience.DEVELOPER: 2,
            ReleaseNotesAudience.DEPENDENCY: 3,
        }[audience]


class AuthorType(enum.StrEnum):
    GITHUB_USER = 'githubUser'


@dataclasses.dataclass(kw_only=True)
class ReleaseNotesAuthor:
    type: AuthorType = AuthorType.GITHUB_USER
    hostname: str
    username: str


@dataclasses.dataclass(kw_only=True)
class ReleaseNoteEntry:
    type: ReleaseNotesType = ReleaseNotesType.STANDARD
    mimetype: str
    contents: str
    category: ReleaseNotesCategory | None = None
    audience: ReleaseNotesAudience | None = None
    author: ReleaseNotesAuthor | None = None
    reference: str | None = None


@dataclasses.dataclass
class ReleaseNotesOcmRef:
    component_name: str
    component_version: str | None


@dataclasses.dataclass
class ReleaseNotesDoc:
    ocm: ReleaseNotesOcmRef | None
    release_notes: list[ReleaseNoteEntry]

    @functools.cached_property
    def component_id(self) -> ocm_model.ComponentIdentity | None:
        if not self.ocm:
            return None

        return ocm_model.ComponentIdentity(
            name=self.ocm.component_name,
            version=self.ocm.component_version,
        )

    @functools.cached_property
    def fname(self) -> str:
        if self.ocm and self.ocm.component_name and self.ocm.component_version:
            # this is usually the case for release notes of sub-components (upgrade-PRs)
            return (
                f'{self.ocm.component_name.replace('/', '_')}_{self.ocm.component_version}'
                f'{RELEASE_NOTES_DOC_SUFFIX}'
            )

        # generate a random filename for now, we might use a stable one later
        return f'{uuid.uuid4()}{RELEASE_NOTES_DOC_SUFFIX}'

    def as_markdown(self) -> str | None:
        if not self.release_notes:
            return None

        header = f'# [{self.ocm.component_name}:{self.ocm.component_version}]'
        categorised_release_notes = collections.defaultdict(list)

        for release_note in self.release_notes:
            if release_note.type is ReleaseNotesType.PRERENDERED:
                if len(self.release_notes) != 1:
                    logger.warning(
                        f'Only expected one prerendered release-note for {self.ocm=} '
                        f'(found {len(self.release_notes)})'
                    )
                return f'{header}\n{release_note.contents.strip()}'
            categorised_release_notes[release_note.category].append(release_note)

        markdown_blocks = []

        sorted_categories = sorted(
            categorised_release_notes.keys(),
            key=lambda cat: ReleaseNotesCategory.category_priority(cat)
        )

        for category in sorted_categories:
            sorted_release_notes = sorted(
                categorised_release_notes[category],
                key=lambda rn: ReleaseNotesAudience.audience_priority(rn.audience)
            )
            title = ReleaseNotesCategory.category_title(category)

            block_lines = [f'## {title}']

            for release_note in sorted_release_notes:
                release_note: ReleaseNoteEntry
                author = f'@{release_note.author.username}'
                audience = release_note.audience.name
                reference = release_note.reference
                # Replace newlines with two spaces _followed by_ newlines, as this is the proper way
                # to do a line-break in a list-item. Also indent the next line, of course.
                content = release_note.contents.strip().replace('\n', '  \n  ')
                block_lines.append(
                    f'- `[{audience}]` {content} by {author} [{reference}]'
                )

            markdown_blocks.append('\n'.join(block_lines))

        return f'{header}\n\n' + '\n\n'.join(markdown_blocks)


r'''
This pattern matches code-blocks in the following format:
```{category} {target_group} [source component name] [reference-dependent str] [author]
{note_message}
```
with the three groups in "[]" being optional by virtue of not being present for commit-attached
release note blocks.

Note: [^\S\n] is "all whitespaces except \n" (or "not [all non-whitespaces and newline]") to
approximate the as-of-yet unsupported \h ([:blank:]) aka "horizontal whitespace"

\x60 -> `
'''
_source_block_pattern = re.compile(
    pattern=(
        r'\x60{3}[^\S\n]*(?P<category>\w+)[^\S\n]+(?P<target_group>\w+)[^\S\n]*'
        r'(?P<source_component_name>\S+)?[^\S\n]?(?P<reference_str>\S+)'
        r'?[^\S\n]?(?P<author>\S+)?[^\S\n]*\n(?P<note>.+?)\n\x60{3}'
    ),
   flags=re.DOTALL | re.IGNORECASE | re.MULTILINE
)


@dataclasses.dataclass(frozen=True)
class SourceBlock:
    '''
    Represents the parsed release note code block within a pull request body or a commit message.

    ```{category} {note_message} [component name] [reference identifier] [author]
    {note_message}
    ```
    with the triple of componentname, reference identifier, and author only being present for code-
    blocks being attached to pull requests
    '''
    source: object
    category: str
    target_group: str
    note_message: str
    component_name: str | None
    author: str | None
    reference_identifier: str | None

    @property
    def identifier(self) -> str:
        ''' returns a human-readable identifier which can be used e.g. for duplicate checking.
        does not include line breaks or spaces
        '''
        return f'[{self.category}<{self.target_group}>]{self.note_message}' \
            .lower().strip().replace(' ', '').replace('\n', '')

    def has_content(self) -> bool:
        ''' checks if there is any content in the source note block
        '''
        if self.note_message.strip().lower() == 'none':
            return False
        return all(z and z.strip() for z in (self.category, self.target_group, self.note_message))

    def as_release_note_entry(
        self,
        hostname: str,
        org: str,
        repo: str,
    ) -> ReleaseNoteEntry:
        repo_url = f'https://{hostname}/{org}/{repo}'

        if isinstance(self.source, git.Commit):
            commit_hexsha = self.source.hexsha
            reference = f'[{org}/{repo}@{commit_hexsha}]({repo_url}/commit/{commit_hexsha})'
            source_username = self.source.author.name
        elif isinstance(self.source, github3.pulls.ShortPullRequest):
            pr_number = self.source.number
            reference = f'[#{pr_number}]({repo_url}/pull/{pr_number})'
            source_username = self.source.user.login
        else:
            raise ValueError(f'unsupported release-notes source: {type(self.source)=}')

        return ReleaseNoteEntry(
            mimetype='text/markdown',
            contents=self.note_message,
            category=ReleaseNotesCategory(self.category.lower()),
            audience=ReleaseNotesAudience(self.target_group.lower()),
            author=ReleaseNotesAuthor(
                hostname=hostname,
                username=self.author or source_username, # prefer author from release-note
            ),
            reference=reference,
        )

    def __hash__(self):
        return hash(self.identifier)

    def __eq__(self, other):
        if isinstance(other, SourceBlock):
            return hash(other) == hash(self)
        return False


def iter_source_blocks(source, content: str) -> tuple[
    list[SourceBlock],
    list[str],
]:
    '''
    Searches for code blocks in release note notation.
    Returns valid blocks and malformed blocks separately.

    :param content: the content to look for release notes in
    :return: returns a tuple with valid blocks first, and malformed second
    '''
    malformed_blocks = []
    valid_blocks = []

    while '<!--' in content:
        comment_start_idx = content.find('<!--')
        comment_stop_idx = content.find('-->', comment_start_idx + len('<!--'))
        content = content[:comment_start_idx] + content[comment_stop_idx + len('-->'):]

    for res in _source_block_pattern.finditer(content.replace('\r\n', '\n')):
        try:
            # component-name might be erroneously parsed if multiple values are specified
            # for target-group using pipe (|) character w/o spaces
            # as those are never valid OCM component-names, ignore those to avoid subsequent
            # processing errors (failure to retrieve component-descriptors/enumerate versions)
            component_name = res.group('source_component_name')
            if component_name and '|' in component_name:
                component_name = None

            if author := res.group('author'):
                author = author.removeprefix('@')

            block = SourceBlock(
                source=source,
                category=res.group('category'),
                target_group=res.group('target_group'),
                note_message=res.group('note'),
                author=author,
                reference_identifier=res.group('reference_str'),
                component_name=component_name,
            )
            if not block.target_group.lower() in ReleaseNotesAudience:
                malformed_blocks.append(res.group())
                continue

            if not block.category.lower() in ReleaseNotesCategory:
                malformed_blocks.append(res.group())
                continue

            if block.has_content():
                valid_blocks.append(block)

        except IndexError as e:
            malformed_blocks.append(res.group())
            logger.debug(f'cannot find group in content: {e}')
            # group not found, ignore
            continue

    return valid_blocks, malformed_blocks


@dataclasses.dataclass(frozen=True)
class ReleaseNotesMetadata:
    checked_at: int
    prs: list[int]


@dataclasses.dataclass(frozen=True)
class MetaPayload:
    type: str
    data: object


def get_meta_obj(
    typ: str,
    data: object
) -> dict:
    return {
        'meta': dataclasses.asdict(MetaPayload(typ, data))
    }
