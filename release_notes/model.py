import dataclasses
import logging
import re
import traceback
import typing

import gci.componentmodel
import git
import github3.pulls

import cnudie.util
import cnudie.retrieve
import version

logger = logging.getLogger(__name__)

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

    def __hash__(self):
        return hash(self.identifier)

    def __eq__(self, other):
        if isinstance(other, ReleaseNote):
            return self.__eq__(other.source_block)
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
    for res in _source_block_pattern.finditer(content.replace('\r\n', '\n')):
        try:
            block = SourceBlock(
                source=source,
                category=res.group('category'),
                target_group=res.group('target_group'),
                note_message=res.group('note'),
                author=res.group('author'),
                reference_identifier=res.group('reference_str'),
                component_name=res.group('source_component_name')
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
class ReleaseNote:
    source_block: SourceBlock

    author: typing.Optional[Author]  # the author of the commit / pull request
    reference: _Reference

    source_component: gci.componentmodel.Component
    is_current_repo: bool
    from_same_github_instance: bool

    def __hash__(self):
        return hash(self.source_block.identifier)

    def __eq__(self, other) -> bool:
        return self.source_block.__eq__(other)

    @property
    def reference_str(self) -> str:
        return f'{self.reference.type.identifier}{self.reference.identifier}'

    @property
    def block_str(self) -> str:
        src_blk = self.source_block
        author = (
            src_blk.author or self.author.username or self.author.display_name.replace(' ', '-')
        )
        if not author.startswith('@'):
            author = '@' + author
        return (
            f'```{src_blk.category} {src_blk.target_group} {self.source_component.name} '
            f'{self.reference_str} {author}\n'
            f'{src_blk.note_message}\n```'
        )


def _source_component(
    component_descriptor_lookup,
    version_lookup,
    current_component: gci.componentmodel.Component,
    source_component_name: str,
) -> gci.componentmodel.Component | None:
    try:
        # try to fetch greatest component-descriptor for source component. The
        # actual version (hopefully) does not matter, as we assume the GithubAccess
        # (which we need to lookup release-notes) will rarely change.
        source_component_descriptor = component_descriptor_lookup(
            gci.componentmodel.ComponentIdentity(
                name=source_component_name,
                version=version.greatest_version(
                    versions=version_lookup(source_component_name),
                    ignore_prerelease_versions=True,
                ),
            ),
        )
        return source_component_descriptor.component
    except Exception:
        logger.warning(
            f'Unable to retrieve component descriptor for source component {source_component_name}'
        )
        traceback.print_exc()
        return None


def create_release_notes_obj(
    component_descriptor_lookup,
    version_lookup,
    source_block: SourceBlock,
    source_component: gci.componentmodel.Component,
    current_component: gci.componentmodel.Component,
) -> ReleaseNote:
    target = source_block.source
    if isinstance(target, git.Commit):
        ref = create_commit_ref(target, source_block)
    elif isinstance(target, github3.pulls.ShortPullRequest):
        ref = create_pull_request_ref(target, source_block)
    else:
        raise NotImplementedError(
            f"Release note creation not implemented for target-type {type(target)}"
        )

    author = author_from_source(target)

    if source_block.component_name:
        source_component = _source_component(
            component_descriptor_lookup=component_descriptor_lookup,
            version_lookup=version_lookup,
            current_component=current_component,
            source_component_name=source_block.component_name,
        )

    # access
    source_component_access = cnudie.util.determine_main_source_for_component(
        component=source_component,
        absent_ok=False
    ).access

    current_src_access = cnudie.util.determine_main_source_for_component(
        component=current_component,
        absent_ok=False
    ).access

    from_same_github_instance = current_src_access.hostname() in source_component_access.hostname()
    is_current_repo = current_component.name == source_component.name

    return ReleaseNote(
        source_block=source_block,
        author=author,
        reference=ref,
        source_component=source_component,
        is_current_repo=is_current_repo,
        from_same_github_instance=from_same_github_instance
    )


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
