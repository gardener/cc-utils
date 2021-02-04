import github3.repos
import typing

import ccc.github


def repositories_for_org(
    github_hostname: str,
    org: str,
) -> typing.List[github3.repos.repo.ShortRepository]:

    github_api = ccc.github.github_api_from_host(host=github_hostname)
    github_org = github_api.organization(org)
    repos = [repo for repo in github_org.repositories()]

    return repos
