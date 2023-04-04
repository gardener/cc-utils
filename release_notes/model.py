import dataclasses
import logging
import re
import typing

import gci.componentmodel
import git
import github3.pulls

import cnudie.util

logger = logging.getLogger(__name__)

'''
This pattern matches code-blocks in the following format:
```{category} {note_message}
{note_message}
```
\x60 -> `
'''
_source_block_pattern = re.compile(
    r'\x60{3}(?P<category>\w+)\s+(?P<target_group>\w+)\n(?P<note>.+?)\n\x60{3}',
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

    @property
    def identifier(self) -> str:
        return str(self.pull_request.number)


def create_commit_ref(commit: git.Commit) -> CommitReference:
    return CommitReference(type=_ref_type_commit, commit=commit)


def create_pull_request_ref(pull_request: github3.pulls.ShortPullRequest) -> PullRequestReference:
    return PullRequestReference(type=_ref_type_pull, pull_request=pull_request)


@dataclasses.dataclass(frozen=True)
class SourceBlock:
    '''Represents the parsed release note code block within a pull request body or a commit message.

    ```{category} {note_message}
    {note_message}
    ```
    '''
    category: str
    target_group: str
    note_message: str

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


def iter_source_blocks(content: str) -> typing.Generator[SourceBlock, None, None]:
    ''' Searches for code blocks in release note notation and returns all found.
    Only valid note blocks are returned, which means that the format has been followed.
    However, it does not check if the category / group exists.

    :param content: the content to look for release notes in
    :return: a list of valid note blocks
    '''
    for res in _source_block_pattern.finditer(content.replace('\r\n', '\n')):
        try:
            block = SourceBlock(category=res.group('category'),
                                target_group=res.group('target_group'),
                                note_message=res.group('note'))
            if block.has_content():
                yield block
        except IndexError as e:
            logger.debug(f'cannot find group in content: {e}')
            # group not found, ignore
            continue


@dataclasses.dataclass(frozen=True)
class ReleaseNote:
    source_commit: git.Commit  # the commit where the release notes were initially gathered from
    source_block: SourceBlock

    raw_body: str  # the raw body of the commit / pull request
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
        author = self.author.username or self.author.display_name.replace(' ', '-')
        return f'```{src_blk.category} {src_blk.target_group} {self.source_component.name} ' \
               f'{self.reference_str} {author}\n' \
               f'{src_blk.note_message}\n```'


def create_release_note_obj(
        source_block: SourceBlock,
        source_commit: git.Commit,
        raw_body: str,
        author: Author,
        target: typing.Union[git.Commit, github3.pulls.ShortPullRequest],
        source_component: gci.componentmodel.Component,
        current_component: gci.componentmodel.Component
) -> ReleaseNote:
    if isinstance(target, git.Commit):
        ref = create_commit_ref(target)
    elif isinstance(target, github3.pulls.ShortPullRequest):
        ref = create_pull_request_ref(target)
    else:
        raise ValueError('either target pull request or commit has to be passed')

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
        source_commit=source_commit,
        source_block=source_block,
        raw_body=raw_body,
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
