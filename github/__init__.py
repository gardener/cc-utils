# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import typing

import github3

RepoUrl: typing.TypeAlias = str
GithubApiLookup = typing.Callable[[RepoUrl], github3.GitHub]


def host_org_and_repo(
    repo_url: str=None,
) -> tuple[str, str, str]:
    '''
    returns a three-tuple of `host`, `org`, `repo`. If repo_url is passed, it is assumed point to
    a github-hosted repository (it may or may not have a schema). Otherwise, fallback to
    environment variables GITHUB_SERVER_URL, GITHUB_REPOSITORY, as set for GitHub-Actions-runs
    is done.
    '''
    if repo_url:
        if '://' in repo_url:
            repo_url = repo_url.split('://')[-1]
        host, org, repo = repo_url.strip('/').split('/')
    else:
        host = os.environ['GITHUB_SERVER_URL'].removeprefix('https://')
        org, repo = os.environ['GITHUB_REPOSITORY'].split('/')

    return host, org, repo


def github_api(
    repo_url: str=None,
    token: str=None,
) -> github3.GitHub:
    '''
    returns an initialised github-api instance, honouring some environment variables typically
    present for GitHub-Actions-runs.

    This function is intended to be used in GitHub-Actions.
    '''
    host, _, _ = host_org_and_repo(
        repo_url=repo_url,
    )

    token = token or os.environ.get('GITHUB_TOKEN')

    if host == 'github.com':
        github_api = github3.GitHub(token=token)
    else:
        api_url = os.environ.get('GITHUB_API_URL', f'https://{host}')
        github_api = github3.GitHubEnterprise(
            url=api_url,
            token=token,
        )

    return github_api
