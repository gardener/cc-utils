# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import collections.abc
import dataclasses
import os
import re
import typing

import github3

RepoUrl: typing.TypeAlias = str
GithubApiLookup = collections.abc.Callable[[RepoUrl], github3.GitHub | None]


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
        server_url = os.environ.get('GITHUB_SERVER_URL', f'https://{host}')
        github_api = github3.GitHubEnterprise(
            url=server_url,
            token=token,
        )

    return github_api


def github_app_api(
    github_app_private_key: str | bytes,
    github_app_id: int,
    repo_url: str | None=None,
) -> github3.GitHub | github3.GitHubEnterprise:
    '''
    returns an initialised github-api instance, which is already logged in using the provided app
    credentials and honouring some environment variables typically present for GitHub-Actions-runs.

    This function is intended to be used in GitHub-Actions.
    '''
    host, org, _ = host_org_and_repo(
        repo_url=repo_url,
    )

    if host == 'github.com':
        github_api = github3.GitHub()
    else:
        server_url = os.environ.get('GITHUB_SERVER_URL', f'https://{host}')
        github_api = github3.GitHubEnterprise(
            url=server_url,
        )

    if isinstance(github_app_private_key, str):
        github_app_private_key = github_app_private_key.encode('utf-8')

    github_api.login_as_app(
        private_key_pem=github_app_private_key,
        app_id=github_app_id,
    )

    installation = github_api.app_installation_for_organization(org)

    github_api.login_as_app_installation(
        private_key_pem=github_app_private_key,
        app_id=github_app_id,
        installation_id=installation.id,
    )

    return github_api


@dataclasses.dataclass
class GitHubAppCredentials:
    private_key: str | bytes
    app_id: int
    host: str
    repo_urls: list[str] | None = None

    def matches(self, repo_url: str) -> bool:
        host, org, repo = host_org_and_repo(repo_url)

        if self.repo_urls is None:
            return self.host == host

        repo_url = '/'.join((host, org, repo))

        for repo_url_regex in self.repo_urls:
            if re.fullmatch(repo_url_regex, repo_url):
                return True

        return False


def github_app_api_lookup(
    github_app_credentials: collections.abc.Sequence[GitHubAppCredentials],
) -> GithubApiLookup:
    def github_api_lookup(
        repo_url: str,
        /,
        absent_ok: bool=False,
    ) -> github3.GitHub | github3.GitHubEnterprise | None:
        for creds in github_app_credentials:
            if not creds.matches(repo_url):
                continue

            return github_app_api(
                github_app_private_key=creds.private_key,
                github_app_id=creds.app_id,
                repo_url=repo_url,
            )

        if absent_ok:
            return None

        raise ValueError(f'no matching GitHub-App credentials for {repo_url=}')

    return github_api_lookup
