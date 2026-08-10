# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import logging

import github3
import github3.issues
from github3.exceptions import NotFoundError
from github3.github import GitHub

logger = logging.getLogger(__name__)


class RepositoryHelperBase:
    def __init__(
        self,
        owner: str,
        name: str,
        github_api: GitHub=None,
        default_branch: str='master',
    ):
        '''
        Args:
            owner (str):    repository owner (also called organisation in GitHub)
            name (str):     repository name
            default_branch (str): branch to use for operations when not specified
            github_api (GitHub): github api to use
        '''
        if not github_api:
            raise ValueError('must pass github_api')

        self.github = github_api

        self.repository = self._create_repository(
            owner=owner,
            name=name
        )
        self.owner = owner
        self.repository_name = name

        self.default_branch = default_branch

    def _create_repository(self, owner: str, name: str):
        try:
            repository = self.github.repository(
                    owner=owner,
                    repository=name
            )
            return repository
        except NotFoundError as nfe:
            try:
                gh_user = self.github.me().name
            except:
                gh_user = 'failed to determine current user'

            raise RuntimeError(
                f'failed to retrieve repository {owner}/{name} {gh_user=}',
                nfe,
            )


def tag_exists(
    repository: github3.repos.Repository,
    tag_name: str,
):
    if not tag_name:
        raise ValueError('tag_name must not be empty')
    try:
        tag_name = tag_name.removesuffix('refs/')
        tag_name = tag_name.removesuffix('tags/')
        repository.ref(f'tags/{tag_name}')
        return True
    except NotFoundError:
        return False


class GitHubRepositoryHelper(RepositoryHelperBase):
    def add_labels_to_pull_request(self, pull_request_number, *labels):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.issue().add_labels(*labels)

    def remove_label_from_pull_request(self, pull_request_number, label):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.issue().remove_label(label)

    def add_comment_to_pr(self, pull_request_number, comment):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.create_comment(comment)

    def is_org_member(self, organization_name, user_login):
        organization = self.github.organization(organization_name)
        return organization.is_member(user_login)

    def is_team_member(self, team_name, user_login) -> bool:
        '''Returns a bool indicating team-membership to the given team for the given user-login

        The team-name is expected in the format `<org-name>/<team-name>`.
        Note: If the team cannot be seen by the user used for the lookup or does not exist `False`
        will be returned.
        '''
        o, t = team_name.split('/')
        org = self.github.organization(o)
        try:
            team = org.team_by_name(t)
            team.membership_for(user_login)
        except github3.exceptions.NotFoundError:
            return False
        else:
            return True


def close_issue(
    issue: github3.issues.ShortIssue,
) -> bool:
    '''
    handle known corner-cases where regular issue close will fail
    comment on issue if closing still fails

    returns True if close was successful, False otherwise
    '''
    def try_close() -> bool:
        try:
            return issue.edit(
                state='closed',
            )

        except github3.exceptions.UnprocessableEntity:
            # likely that assignee was suspended from github

            if not issue.assignees:
                raise

            issue.remove_assignees(issue.assignees)
            return issue.edit(
                state='closed',
            )

    closed = try_close()
    if not closed:
        issue.create_comment('unable to close ticket')

    return closed
