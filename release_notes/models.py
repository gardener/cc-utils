import dataclasses
import re
import typing

import gci.componentmodel
import git
import github3.pulls
from git import Commit

import cnudie.util


@dataclasses.dataclass
class Author:
    # for pull requests
    username: str  # the GitHub username

    # for commits
    display_name: str  # the name which authored the commit
    email: str  # the email the commit was authored

    def __str__(self) -> str:
        if self.username and self.username.strip():
            return f"@{self.username}"
        if self.display_name and self.display_name.strip():
            return f"`{self.display_name} <{self.email}>`"
        return ""


def author_from_commit(commit: git.Commit) -> Author:
    return Author("", commit.author.name, commit.author.email)


def author_from_pull_request(pull_request: github3.pulls.ShortPullRequest) -> Author:
    return Author(pull_request.user.login, "", "")


#

@dataclasses.dataclass
class ReferenceType:
    identifier: str  # identifier for release note block
    prefix: str  # prefix for generated release notes


REF_TYPE_PULL = ReferenceType(identifier="#", prefix="#")
REF_TYPE_COMMIT = ReferenceType(identifier="$", prefix="@")

REF_TYPES = [REF_TYPE_PULL, REF_TYPE_COMMIT]


@dataclasses.dataclass
class Reference:
    type: ReferenceType

    def get_identifier(self) -> str:
        raise NotImplementedError("get_content not implemented yet")


@dataclasses.dataclass
class CommitReference(Reference):
    commit: git.Commit

    def get_identifier(self) -> str:
        return self.commit.hexsha


@dataclasses.dataclass
class PullRequestReference(Reference):
    pull_request: github3.pulls.ShortPullRequest

    def get_identifier(self) -> str:
        return str(self.pull_request.number)


def create_commit_ref(commit: git.Commit) -> CommitReference:
    return CommitReference(type=REF_TYPE_COMMIT, commit=commit)


def create_pull_request_ref(pull_request: github3.pulls.ShortPullRequest) -> PullRequestReference:
    return PullRequestReference(type=REF_TYPE_PULL, pull_request=pull_request)


@dataclasses.dataclass
class SourceBlock:
    category: str
    target_group: str
    note_message: str

    def get_identifier(self) -> str:
        """ returns a human-readable identifier which can be used e.g. for duplicate checking.
        does not include line breaks or spaces
        """
        return f"[{self.category}<{self.target_group}>]{self.note_message}" \
            .lower().strip().replace(" ", "").replace("\n", "")

    def has_content(self) -> bool:
        """ checks if there is any content in the source note block
        """
        if self.note_message.strip().lower() == "none":
            return False
        return all(z and z.strip() for z in (self.category, self.target_group, self.note_message))

    def __hash__(self):
        return hash(self.get_identifier())

    def __eq__(self, other):
        if isinstance(other, ReleaseNote):
            return self.__eq__(other.source_block)
        if isinstance(other, SourceBlock):
            return hash(other) == hash(self)
        return False


pattern = re.compile(r"\x60{3}(?P<category>\w+)\s+(?P<target_group>\w+)\n(?P<note>.+?)\n\x60{3}",
                     flags=re.DOTALL | re.IGNORECASE | re.MULTILINE)


def list_source_blocks(content: str) -> typing.Generator[SourceBlock, None, None]:
    """ Searches for code blocks in release note notation and returns all found.
    Only valid note blocks are returned, which means that the format has been followed.
    However, it does not check if the category / group exists.

    :param content: the content to look for release notes in
    :return: a list of valid note blocks
    """
    for res in pattern.finditer(content.replace("\r\n", "\n")):
        try:
            block = SourceBlock(category=res.group("category"),
                                target_group=res.group("target_group"),
                                note_message=res.group("note"))
            if block.has_content():
                yield block
        except IndexError:
            # group not found, ignore
            continue


@dataclasses.dataclass
class ReleaseNote:
    source_commit: Commit  # the commit where the release notes were initially gathered from
    source_block: SourceBlock

    raw_body: str  # the raw body of the commit / pull request
    author: typing.Optional[Author]  # the author of the commit / pull request
    reference: Reference

    source_component: gci.componentmodel.Component = dataclasses.field(compare=False)
    is_current_repo: bool
    from_same_github_instance: bool

    def __hash__(self):
        return hash(self.source_block.get_identifier())

    def __eq__(self, other) -> bool:
        return self.source_block.__eq__(other)

    def get_author_str(self) -> str:
        return str(self.author)

    def get_reference_str(self) -> str:
        return f"{self.reference.type.identifier}{self.reference.get_identifier()}"

    def to_block_str(self) -> str:
        return "```{category} {group} {src_repo} {ref} {author}\n{message}\n```".format(
            category=self.source_block.category,
            group=self.source_block.target_group,
            src_repo=self.source_component.name,
            ref=self.get_reference_str(),
            author=self.author.username or self.author.display_name.replace(" ", "-"),
            message=self.source_block.note_message
        )


def create_release_note_obj(
        block: SourceBlock,

        source_commit: Commit,
        raw_body: str,
        author: typing.Optional[Author],

        targets: typing.Union[git.Commit, github3.pulls.ShortPullRequest],

        source_component: gci.componentmodel.Component,
        current_component: gci.componentmodel.Component
) -> ReleaseNote:
    if isinstance(targets, git.Commit):
        ref = create_commit_ref(targets)
    elif isinstance(targets, github3.pulls.ShortPullRequest):
        ref = create_pull_request_ref(targets)
    else:
        raise ValueError("either target pull request or commit has to be passed")

    # access
    source_component_access = cnudie.util.determine_main_source_for_component(
        component=source_component,
        absent_ok=False
    ).access
    current_component_access = cnudie.util.determine_main_source_for_component(
        component=current_component,
        absent_ok=False
    ).access

    from_same_github_instance = current_component_access.hostname() in source_component_access.hostname()
    is_current_repo = current_component.name == source_component.name

    return ReleaseNote(
        source_commit, block, raw_body, author, ref, source_component,
        is_current_repo, from_same_github_instance
    )
