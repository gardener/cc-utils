import collections
import dataclasses
import enum
import functools
import logging
import re
import typing
import uuid

import ocm as ocm_model
import git
import github3.pulls

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
    pullrequest: str | None = None


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

        header = f'[{self.ocm.component_name}:{self.ocm.component_version}]'
        categorized_entries = collections.defaultdict(list)

        for entry in self.release_notes:
            if entry.type is ReleaseNotesType.PRERENDERED:
                if len(self.release_notes) != 1:
                    raise RuntimeError(
                        f'Only expected one prerendered release-note for {self.ocm=} '
                        f'(found {len(self.release_notes)})'
                    )
                return f'{header}\n{entry.contents.strip()}'
            categorized_entries[entry.category].append(entry)

        markdown_blocks = []
        # Sort categories by priority
        sorted_categories = sorted(
            categorized_entries.keys(),
            key=lambda cat: ReleaseNotesCategory.category_priority(cat)
        )

        for category in sorted_categories:
            entries = categorized_entries[category]
            title = ReleaseNotesCategory.category_title(category)

            block_lines = [f'## {title}']

            for entry in entries:
                entry: ReleaseNoteEntry
                author = f'@{entry.author.username}'
                audience = entry.audience
                pullrequest = entry.pullrequest
                block_lines.append(
                    f'- `[{audience.name}]` {entry.contents.strip()} by {author} [{pullrequest}]'
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
approxmiate the as-of-yet unsupported \h ([:blank:]) aka "horizontal whitespace"

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
class Author:
    # for pull requests
    username: str  # the GitHub username

    # for commits
    display_name: str  # the name which authored the commit
    email: str  # the email the commit was authored

    def __str__(self) -> str:
        if self.username and self.username.strip():
            return f'@{self.username}'
        if self.display_name and self.display_name.strip():
            return f'`{self.display_name} <{self.email}>`'
        return ''


def author_from_commit(commit: git.Commit) -> Author:
    return Author(
        username='',
        display_name=commit.author.name,
        email=commit.author.email
    )


def author_from_pull_request(pull_request: github3.pulls.ShortPullRequest) -> Author:
    return Author(
        username=pull_request.user.login,
        display_name='',
        email=''
    )


def author_from_source(source: git.Commit | github3.pulls.ShortPullRequest) -> Author:
    if isinstance(source, git.Commit):
        return author_from_commit(source)
    elif isinstance(source, github3.pulls.ShortPullRequest):
        return author_from_pull_request(source)
    else:
        raise NotImplementedError(type(source))


@dataclasses.dataclass(frozen=True)
class _ReferenceType:
    identifier: str  # identifier for release note block
    prefix: str  # prefix for generated release notes


_ref_type_pull = _ReferenceType(identifier='#', prefix='#')
_ref_type_commit = _ReferenceType(identifier='$', prefix='@')

_ref_types = (_ref_type_pull, _ref_type_commit)


@dataclasses.dataclass(frozen=True)
class _Reference:
    ''' Represents where a release note comes from, for example through a
    commit or a pull request.

    _Reference is only a superclass for a commit- or pull request-reference,
    which have their own classes to access the pull request or the commit
    objects: `CommitReference` and `PullRequestReference`.  '''
    type: _ReferenceType

    @property
    def identifier(self) -> str:
        ''' The identifier for the reference - can be a commit hash, for
        example, or the number of the pull request.  '''
        raise NotImplementedError('get_content not implemented yet')


@dataclasses.dataclass(frozen=True)
class CommitReference(_Reference):
    ''' Represents the commit where the release note came from
    '''
    commit: git.Commit

    @property
    def identifier(self) -> str:
        return self.commit.hexsha


@dataclasses.dataclass(frozen=True)
class PullRequestReference(_Reference):
    ''' Represents the pull requests where the release note came from
    '''
    pull_request: github3.pulls.ShortPullRequest
    source_block: 'SourceBlock'

    @property
    def identifier(self) -> str:
        if self.source_block.reference_identifier:
            return self.source_block.reference_identifier.strip('#')
        return str(self.pull_request.number)


@dataclasses.dataclass(frozen=True)
class SourceBlock:
    '''Represents the parsed release note code block within a pull request body or a commit message.

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
        hostname: str
    ) -> ReleaseNoteEntry:
        return ReleaseNoteEntry(
            mimetype='text/markdown',
            contents=self.note_message,
            category=ReleaseNotesCategory(self.category.lower()),
            audience=ReleaseNotesAudience(self.target_group.lower()),
            author=ReleaseNotesAuthor(
                hostname=hostname,
                username=self.author,
            ),
            pullrequest=self.reference_identifier,
        )

    def __hash__(self):
        return hash(self.identifier)

    def __eq__(self, other):
        if isinstance(other, SourceBlock):
            return hash(other) == hash(self)
        return False


def create_commit_ref(commit: git.Commit, source_block: SourceBlock) -> CommitReference:
    return CommitReference(type=_ref_type_commit, commit=commit)


def create_pull_request_ref(
        pull_request: github3.pulls.ShortPullRequest,
        source_block: SourceBlock,
    ) -> PullRequestReference:
    return PullRequestReference(
        type=_ref_type_pull,
        pull_request=pull_request,
        source_block=source_block,
    )


def iter_source_blocks(source, content: str) -> typing.Generator[SourceBlock, None, None]:
    ''' Searches for code blocks in release note notation and returns all found.
    Only valid note blocks are returned, which means that the format has been followed.
    However, it does not check if the category / group exists.

    :param content: the content to look for release notes in
    :return: a list of valid note blocks
    '''
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

            block = SourceBlock(
                source=source,
                category=res.group('category'),
                target_group=res.group('target_group'),
                note_message=res.group('note'),
                author=res.group('author'),
                reference_identifier=res.group('reference_str'),
                component_name=component_name,
            )
            if not block.target_group.lower() in ['user', 'operator', 'developer', 'dependency']:
                continue
            if block.has_content():
                yield block
        except IndexError as e:
            logger.debug(f'cannot find group in content: {e}')
            # group not found, ignore
            continue


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
