# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import dataclasses
import enum
import logging
from pathlib import Path
import typing
import urllib.parse

from github3 import GitHub
from github3.exceptions import NotFoundError

from github.util import GitHubRepositoryHelper

from ci.util import existing_dir, existing_file, not_none, warning
import ci.log

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


class CodeownersEnumerator:
    '''
    Parses GitHub CODEOWNSERS files [0] from the documented default locations for a given
    (git) repository work tree into a stream of codeowners entries.

    [0] https://help.github.com/articles/about-codeowners/
    '''
    CODEOWNERS_PATHS = ('CODEOWNERS', '.github/CODEOWNERS', 'docs/CODEOWNERS')

    def enumerate_single_file(self, file_path: str):
        file_path = existing_file(file_path)
        with open(file_path) as f:
            yield from self._filter_codeowners_entries(f.readlines())

    def enumerate_local_repo(self, repo_dir: str):
        repo_dir = existing_dir(Path(repo_dir))
        if not repo_dir.joinpath('.git').is_dir():
            raise ValueError(f'not a git root directory: {self.repo_dir}')

        for path in self.CODEOWNERS_PATHS:
            codeowners_file = repo_dir.joinpath(path)
            if codeowners_file.is_file():
                with open(codeowners_file) as f:
                    yield from self._filter_codeowners_entries(f.readlines())

    def enumerate_remote_repo(self, github_repo_helper: GitHubRepositoryHelper):
        for path in self.CODEOWNERS_PATHS:
            try:
                yield from self._filter_codeowners_entries(
                    github_repo_helper.retrieve_text_file_contents(file_path=path).split('\n')
                )
            except NotFoundError:
                pass # ignore absent files

    def _filter_codeowners_entries(self, lines):
        '''
        returns a generator yielding parsed entries from */CODEOWNERS
        each entry may be one of
         - a github user name (with a leading @ character)
         - a github team name (leading @ character and exactly one / character (org/name))
         - an email address
        '''
        for line in lines:
            line = line.strip()
            if line.startswith('#'):
                continue
            # Yield tokens, ignoring the first (it is the path filter)
            yield from line.split()[1:]


def _first(iterable):
    try:
        return next(iterable)
    except StopIteration:
        return None


@dataclasses.dataclass(frozen=True)
class CodeOwnerGithubUser:
    user: str
    source: str


@dataclasses.dataclass(frozen=True)
class CodeOwnerPersonalName:
    firstName: str
    lastName: str
    source: str


@dataclasses.dataclass(frozen=True)
class CodeOwnerEmail:
    email: str
    source: str


@dataclasses.dataclass
class CodeOwner:
    github: typing.Optional[CodeOwnerGithubUser]
    personalName: typing.Optional[CodeOwnerPersonalName]
    email: typing.Optional[CodeOwnerEmail]

    @staticmethod
    def create(
        github_username: typing.Optional[str] = None,
        github_source: typing.Optional[str] = None,
        email: typing.Optional[str] = None,
        email_source: typing.Optional[str] = None,
        full_name: typing.Optional[str] = None,
        full_name_source: typing.Optional[str] = None,
    ) -> 'CodeOwner':
        '''
        convenience method to create a 'CodeOwner' obj
        if an attribute is `None`, its source is ignored
        '''
        github = None
        name = None
        email_obj = None

        if github_username:
            github = CodeOwnerGithubUser(
                user=github_username,
                source=github_source or None,
            )

        if full_name:
            nameparts = full_name.split(' ')
            first, last = ' '.join(nameparts[:-1]), nameparts[-1:][0]
            name = CodeOwnerPersonalName(
                firstName=first,
                lastName=last,
                source=full_name_source or None,
            )

        if email:
            email_obj = CodeOwnerEmail(
                email=email,
                source=email_source or None,
            )

        return CodeOwner(
            github=github or None,
            personalName=name or None,
            email=email_obj or None,
        )


class CodeOwnerMetadataTypes(enum.Enum):
    PERSONAL_NAME = 'personalName'
    GITHUB_USER = 'githubUser'
    EMAIL = 'email'
    UNKNOWN = 'unknown'

    @staticmethod
    def for_codeowner_attribute(
        attribute: typing.Union[CodeOwnerGithubUser, CodeOwnerEmail, CodeOwnerPersonalName],
    ) -> 'CodeOwnerMetadataTypes':
        if isinstance(attribute, CodeOwnerGithubUser):
            return CodeOwnerMetadataTypes.GITHUB_USER
        elif isinstance(attribute, CodeOwnerEmail):
            return CodeOwnerMetadataTypes.EMAIL
        elif isinstance(attribute, CodeOwnerPersonalName):
            return CodeOwnerMetadataTypes.PERSONAL_NAME
        else:
            return CodeOwnerMetadataTypes.UNKNOWN


def iter_codeowner_attribute_dict(
    codeowner: CodeOwner,
) -> typing.Generator[dict, None, None]:
    for metadata in codeowner.__dict__.values():
        if not metadata:
            continue
        attribute_dict = dataclasses.asdict(metadata)
        attribute_dict['type'] = CodeOwnerMetadataTypes.for_codeowner_attribute(metadata).value
        yield attribute_dict


class CodeOwnerEntryResolver:
    '''
    Resolves GitHub CODEOWNERS entries [0] into email addresses.

    The github3.py api object needs to be pre-authenticated with the privilege to read
    organisation and team memberhip data.

    [0] https://help.github.com/articles/about-codeowners/
    '''

    def __init__(self, github_api: GitHub):
        self.github_api = not_none(github_api)

    def _determine_email_address(self, github_user_name: str):
        not_none(github_user_name)
        try:
            user = self.github_api.user(github_user_name)
        except NotFoundError:
            logger.warning(f'failed to lookup {github_user_name=} {self.github_api._github_url=}')
            return None

        return user.email

    def _resolve_team_members(self, github_team_name: str):
        not_none(github_team_name)
        org_name, team_name = github_team_name.split('/') # always of form 'org/name'
        organisation = self.github_api.organization(org_name)
        # unfortunately, we have to look-up the team (no api to retrieve it by name)
        team_or_none = _first(filter(lambda team: team.slug == team_name, organisation.teams()))
        if not team_or_none:
            warning('failed to lookup team {t}'.format(t=team_name))
            return []
        for member in map(self.github_api.user, team_or_none.members()):
            if member.email:
                yield member.email
            else:
                warning(f'no email found for GitHub user {member}')

    def iter_codeowners_for_team_name(
        self,
        github_team_name: str,
        source: str,
    ) -> typing.Generator[CodeOwner, None, None]:
        if not github_team_name:
            raise RuntimeError(f'{github_team_name} must not be empty')

        org_name, team_name = github_team_name.split('/') # always of form 'org/name'
        organisation = self.github_api.organization(org_name)
        team_or_none = _first(filter(lambda team: team.slug == team_name, organisation.teams()))

        if not team_or_none:
            logger.warning(f'failed to lookup team {team_name}')
            return

        for member in team_or_none.members():
            gh_user = self.github_api.user(member)
            gh_api_hostname = urllib.parse.urlparse(self.github_api._github_url).netloc

            yield CodeOwner.create(
                github_username=member.login,
                github_source=source,
                full_name=gh_user.name,
                full_name_source=gh_api_hostname,
                email=gh_user.email,
                email_source=gh_api_hostname,
            )

    def resolve_email_addresses(self, codeowners_entries):
        '''
        returns a generator yielding the resolved email addresses for the given iterable of
        github codeowners entries.
        '''
        for codeowner_entry in codeowners_entries:
            if '@' not in codeowner_entry:
                warning(f'invalid codeowners-entry: {codeowner_entry}')
                continue
            if not codeowner_entry.startswith('@'):
                yield codeowner_entry # plain email address
            elif '/' not in codeowner_entry:
                email_addr = self._determine_email_address(codeowner_entry[1:])
                if email_addr:
                    yield email_addr
                else:
                    continue
            else:
                yield from self._resolve_team_members(codeowner_entry[1:])

    def iter_codeowners(
        self,
        codeowner_entries,
        source: typing.Optional[str],
    ) -> typing.Generator[CodeOwner, None, None]:
        gh_api_hostname = urllib.parse.urlparse(self.github_api._github_url).netloc
        for codeowner_entry in codeowner_entries:
            # faulty CODEOWNERS
            if '@' not in codeowner_entry:
                logger.warning(f'invalid codeowners-entry: {codeowner_entry}')
                return

            # email
            if not codeowner_entry.startswith('@'):
                yield CodeOwner.create(
                    email=codeowner_entry,
                    email_source=source,
                )
            # username
            elif '/' not in codeowner_entry:
                username = codeowner_entry[1:]

                try:
                    # user found for username
                    gh_user = self.github_api.user(username)
                    yield CodeOwner.create(
                        github_username=username,
                        github_source=source,
                        email=gh_user.email,
                        email_source=gh_api_hostname,
                        full_name=gh_user.name,
                        full_name_source=gh_api_hostname,
                    )
                except NotFoundError:
                    # no user found
                    logger.warning(f'{username=} not found')
                    yield CodeOwner.create(
                        github_username=username,
                        github_source=source,
                    )

            # team_name
            else:
                team_name = codeowner_entry[1:]
                yield from self.iter_codeowners_for_team_name(
                    github_team_name=team_name,
                    source=source,
                )


def find_codeowner_by_attribute(
    codeowners: typing.List[CodeOwner],
    user: typing.Optional[CodeOwnerGithubUser],
    email: typing.Optional[CodeOwnerEmail],
) -> typing.Tuple[CodeOwner, int]:
    '''
    search codeowners for attributes
    returns found codeowners and its index in codeowners
    if no codeowner is found, (`None`, `None`) is returned
    '''
    if not bool(user or email):
        return None, None

    for codeowner in codeowners:
        if user:
            if codeowner.github:
                if codeowner.github.user == user.user:
                    return codeowner, codeowners.index(codeowner)
        if email:
            if codeowner.email:
                if codeowner.email.email == email.email:
                    return codeowner, codeowners.index(codeowner)

    return None, None


