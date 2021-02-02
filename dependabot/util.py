import github3.repos
import typing

import ccc.github


def repositories_for_org(
    full_org_name: str,  # e.g. github.com/gardener
) -> typing.List[github3.repos.repo.ShortRepository]:

    host, org = full_org_name.split('/')
    github_api = ccc.github.github_api_from_host(host=host)
    github_org = github_api.organization(org)
    repos = [repo for repo in github_org.repositories()]

    return repos
