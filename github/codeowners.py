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

from github3 import GitHub
from github3.exceptions import NotFoundError
import github3.repos.repo

from ci.util import existing_dir, existing_file, not_none
import ci.log

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


def enumerate_codeowners_from_remote_repo(
    repo: github3.repos.repo.Repository,
    paths: typing.Iterable[str] = ('CODEOWNERS', '.github/CODEOWNERS', 'docs/CODEOWNERS'),
) -> typing.Generator[str, None, None]:
    for path in paths:
        try:
            yield from filter_codeowners_entries(
                repo.file_contents(path=path).decoded.decode('utf-8').split('\n')
            )
        except NotFoundError:
            pass # ignore absent files


def enumerate_codeowners_from_file(
    file_path: str,
) -> typing.Generator[str, None, None]:
    file_path = existing_file(file_path)
    with open(file_path) as f:
        yield from filter_codeowners_entries(f.readlines())


def enumerate_codeowners_from_local_repo(
    repo_dir: str,
    paths: typing.Iterable[str] = ('CODEOWNERS', '.github/CODEOWNERS', 'docs/CODEOWNERS'),
) -> typing.Generator[str, None, None]:
    repo_dir = existing_dir(Path(repo_dir))
    if not repo_dir.joinpath('.git').is_dir():
        raise ValueError(f'not a git root directory: {repo_dir}')

    for path in paths:
        codeowners_file = repo_dir.joinpath(path)
        if codeowners_file.is_file():
            with open(codeowners_file) as f:
                yield from filter_codeowners_entries(f.readlines())


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


def _first(
    iterable: typing.Iterable,
):
    try:
        return next(iterable)
    except StopIteration:
        return None


def determine_email_address(
    github_user_name: str,
    github_api: GitHub,
) -> typing.Optional[str]:
    not_none(github_user_name)
    try:
        user = github_api.user(github_user_name)
    except NotFoundError:
        logger.warning(f'failed to lookup {github_user_name=} {github_api._github_url=}')
        return None

    return user.email


def resolve_team_members(
    github_team_name: str,
    github_api: GitHub,
) -> typing.Union[typing.Generator[str, None, None], list]:
    not_none(github_team_name)
    org_name, team_name = github_team_name.split('/') # always of form 'org/name'
    organisation = github_api.organization(org_name)
    # unfortunately, we have to look-up the team (no api to retrieve it by name)
    team_or_none = _first(filter(lambda team: team.slug == team_name, organisation.teams()))
    if not team_or_none:
        logger.warning('failed to lookup team {t}'.format(t=team_name))
        return []
    for member in map(github_api.user, team_or_none.members()):
        if member.email:
            yield member.email
        else:
            logger.warning(f'no email found for GitHub user {member}')


def resolve_email_addresses(
    codeowners_entries,
    github_api: GitHub,
) -> typing.Generator[str, None, None]:
    '''
    returns a generator yielding the resolved email addresses for the given iterable of
    github codeowners entries.
    '''
    for codeowner_entry in codeowners_entries:
        if '@' not in codeowner_entry:
            logger.warning(f'invalid codeowners-entry: {codeowner_entry}')
            continue
        if not codeowner_entry.startswith('@'):
            yield codeowner_entry # plain email address
        elif '/' not in codeowner_entry:
            email_addr = determine_email_address(
                github_user_name=codeowner_entry[1:],
                github_api=github_api,
            )
            if email_addr:
                yield email_addr
            else:
                continue
        else:
            yield from resolve_team_members(
                github_team_name=codeowner_entry[1:],
                github_api=github_api,
            )
