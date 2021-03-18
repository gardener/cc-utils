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
from github3 import GitHub
from github3.exceptions import NotFoundError

from github.util import GitHubRepositoryHelper

from ci.util import existing_dir, existing_file, not_none, warning


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
        user = self.github_api.user(github_user_name)
        return user.email

    def _resolve_team_members(self, github_team_name: str):
        not_none(github_team_name)
        org_name, team_name = github_team_name.split('/') # always of form 'org/name'
        organisation = self.github_api.organization(org_name)
        # unfortunately, we have to look-up the team (no api to retrieve it by name)
        team_or_none = _first(filter(lambda team: team.name == team_name, organisation.teams()))
        if not team_or_none:
            warning('failed to lookup team {t}'.format(t=team_name))
            return []
        for member in map(self.github_api.user, team_or_none.members()):
            if member.email:
                yield member.email
            else:
                warning(f'no email found for GitHub user {member}')

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
