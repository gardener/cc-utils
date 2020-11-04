import tempfile

import ccc.github
import whitesource.client

import gci.componentmodel as cm


def get_post_project_object(
    whitesource_client: whitesource.client.WhitesourceClient,
    component: cm.Component,
    product_token: str,
):
    github_api = ccc.github.github_api_from_component(component=component)
    github_repo = ccc.github.GithubRepo.from_component(component)

    return PostProjectObject(
        whitesource_client=whitesource_client,
        github_api=github_api,
        product_token=product_token,
        component=component,
        github_repo=github_repo,
        component_version=component.version,
    )


class PostProjectObject:
    def __init__(
        self,
        whitesource_client: whitesource.client.WhitesourceClient,
        github_api,
        component: cm.Component,
        product_token: str,
        github_repo: ccc.github.GithubRepo,
        component_version,
    ):
        self.whitesource_client = whitesource_client
        self.github_api = github_api
        self.component = component
        self.product_token = product_token
        self.github_repo = github_repo
        self.component_version = component_version


def download_component(
    github_api,
    github_repo: ccc.github.GithubRepo,
    dest: tempfile.TemporaryFile,
    ref: str,
):

    repo = github_api.repository(
        github_repo.org_name,
        github_repo.repo_name,
    )

    url = repo._build_url(
        'tarball',
        ref,
        base_url=repo._api,
    )
    res = repo._get(
        url,
        allow_redirects=True,
        stream=True,
    )
    if not res.ok:
        raise RuntimeError(
            f'request to download github zip archive from {url=}'
            f' failed with {res.status_code=} {res.reason=}'
        )

    for chunk in res.iter_content(chunk_size=512):
        dest.write(chunk)

    dest.flush()
    dest.seek(0)
