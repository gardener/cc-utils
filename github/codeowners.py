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
from pathlib import Path
import logging
import typing

from github3 import GitHub, GitHubEnterprise
from github3.exceptions import NotFoundError
import github3.orgs
import github3.repos.repo
import github3.search.user
import github3.users

from ci.util import existing_dir, existing_file, not_none
import ci.log

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


class Username(str):
    pass


class Team(str):
    @property
    def org_name(self) -> str:
        return self.split('/')[0]

    @property
    def name(self) -> str:
        return self.split('/')[1]


class EmailAddress(str):
    pass


def parse_codeowner_entry(
    entry: str,
) -> Username | EmailAddress | Team:
    '''
    Parse codeowner entry to Username, Email or Team.
    Invalid entries return `None`.
    '''

    if '@' not in entry:
        logger.warning(f'invalid codeowners-entry: {entry}')
        return

    if not entry.startswith('@'):
        return EmailAddress(entry) # plain email address

    entry = entry.removeprefix('@')

    if '/' not in entry:
        return Username(entry)

    else:
        return Team(entry)


def enumerate_codeowners_from_remote_repo(
    repo: github3.repos.repo.Repository,
    paths: typing.Iterable[str] = ('CODEOWNERS', '.github/CODEOWNERS', 'docs/CODEOWNERS'),
) -> typing.Generator[Username | Team | EmailAddress, None, None]:
    for path in paths:
        try:
            yield from (
                parse_codeowner_entry(entry)
                for entry in filter_codeowners_entries(
                    repo.file_contents(path=path).decoded.decode('utf-8').split('\n'),
                )
            )
        except NotFoundError:
            pass # ignore absent files


def enumerate_codeowners_from_file(
    file_path: str,
) -> typing.Generator[Username | Team | EmailAddress, None, None]:
    file_path = existing_file(file_path)
    with open(file_path) as f:
        yield from (
            parse_codeowner_entry(entry)
            for entry in filter_codeowners_entries(f.readlines())
        )


def enumerate_codeowners_from_local_repo(
    repo_dir: str,
    paths: typing.Iterable[str] = ('CODEOWNERS', '.github/CODEOWNERS', 'docs/CODEOWNERS'),
) -> typing.Generator[Username | Team | EmailAddress, None, None]:
    repo_dir = existing_dir(Path(repo_dir))
    if not repo_dir.joinpath('.git').is_dir():
        raise ValueError(f'not a git root directory: {repo_dir}')

    for path in paths:
        codeowners_file = repo_dir.joinpath(path)
        if codeowners_file.is_file():
            with open(codeowners_file) as f:
                yield from (
                    parse_codeowner_entry(entry)
                    for entry in filter_codeowners_entries(f.readlines())
                )


def filter_codeowners_entries(
    lines: typing.Iterable[str],
) -> typing.Generator[str, None, None]:
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


def determine_email_address(
    github_user_name: str | Username,
    github_api: GitHub,
) -> EmailAddress | None:
    '''
    Return email address exposed for given user.
    `None` returned if either user not found, or no email address is exposed.
    '''
    not_none(github_user_name)
    try:
        user = github_api.user(github_user_name)
    except NotFoundError:
        logger.warning(f'failed to lookup {github_user_name=} {github_api._github_url=}')
        return None

    if not user.email:
        return None

    return EmailAddress(user.email)


def usernames_from_email_address(
    email_address: EmailAddress,
    gh_api: GitHub | GitHubEnterprise,
) -> typing.Generator[Username, None, None]:
    no_user = True
    for res in gh_api.search_users(query=f'{email_address} in:email'):
        no_user = False
        yield Username(res.user.login)

    if no_user:
        logger.warning(f'no user found for {email_address=}')


def resolve_team_members(
    team: Team,
    github_api: GitHub,
) -> typing.Generator[Username, None, None]:
    '''
    Return generator yielding usernames resolved recursively from given team.
    If no team found for given team, no users are returned.
    '''
    organisation = github_api.organization(team.org_name)
    try:
        team = organisation.team_by_name(team.name)
        team: github3.orgs.Team
    except NotFoundError:
        logger.warning('failed to lookup team {t}'.format(t=team.name))
        return

    yield from (
        Username(member.login)
        for member in team.members()
    )


def resolve_email_addresses(
    codeowners_entries: typing.Iterable[Username | EmailAddress | Team],
    github_api: GitHub,
) -> typing.Generator[EmailAddress, None, None]:
    '''
    Returns a generator yielding the resolved email addresses for the given iterable of
    github codeowners entries.
    Teams are resolved to Users recursively.
    Users are resolved to exposed email addresses.
    If no email address is exposed the User is skipped.
    '''
    unique_email_addresses = set()

    for codeowner_entry in codeowners_entries:
        if isinstance(codeowner_entry, EmailAddress):
            unique_email_addresses.add(codeowner_entry)
            continue

        if isinstance(codeowner_entry, Username):
            if (email_address := determine_email_address(
                github_user_name=codeowner_entry,
                github_api=github_api,
            )):
                unique_email_addresses.add(email_address)
                continue

        if isinstance(codeowner_entry, Team):
            for email_address in resolve_email_addresses(
                codeowners_entries=resolve_team_members(
                    team=codeowner_entry,
                    github_api=github_api,
                ),
                github_api=github_api,
            ):
                unique_email_addresses.add(email_address)
            continue

    yield from unique_email_addresses


def resolve_usernames(
    codeowners_entries: typing.Iterable[Username | EmailAddress | Team],
    github_api: GitHub,
) -> typing.Generator[Username, None, None]:
    '''
    Returns a generator yielding the resolved usernames for the given iterable of
    github codeowners entries.
    Teams are resolved to Users recursively.
    Emails are resolved to users.
    If no username is found for given email address, its skipped.
    '''
    unique_usernames = set()

    for codeowner_entry in codeowners_entries:
        if isinstance(codeowner_entry, Username):
            unique_usernames.add(codeowner_entry)
            continue

        if isinstance(codeowner_entry, EmailAddress):
            for username in usernames_from_email_address(
                email_address=codeowner_entry,
                gh_api=github_api,
            ):
                unique_usernames.add(username)

            continue

        if isinstance(codeowner_entry, Team):
            for username in resolve_usernames(
                codeowners_entries=resolve_team_members(
                    team=codeowner_entry,
                    github_api=github_api,
                ),
                github_api=github_api,
            ):
                unique_usernames.add(username)

            continue

    yield from unique_usernames
