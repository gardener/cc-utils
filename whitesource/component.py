import tempfile

import ccc.github
import product.model
import whitesource.client


def get_post_project_object(whitesource_client: whitesource.client.WhitesourceClient,
                            component: product.model.Component,
                            product_token: str):
    # get component_name
    if isinstance(component.name(), str):
        component_name = product.model.ComponentName.from_github_repo_url(component.name())
    elif isinstance(component.name(), product.model.ComponentName):
        component_name = component.name()
    else:
        raise NotImplementedError

    github_api = ccc.github.github_api_from_component(component=component)
    return PostProjectObject(
        whitesource_client=whitesource_client,
        github_api=github_api,
        product_token=product_token,
        component=component,
        component_name=component_name
    )


class PostProjectObject:
    def __init__(self,
                 whitesource_client: whitesource.client.WhitesourceClient,
                 github_api,
                 component: product.model.Component,
                 product_token: str,
                 component_name: product.model.ComponentName):
        self.whitesource_client = whitesource_client
        self.github_api = github_api
        self.component = component
        self.product_token = product_token
        self.component_name = component_name


def download_component(github_api,
                       component_name: product.model.ComponentName,
                       dest: tempfile.TemporaryFile,
                       ref: str):
    repo = github_api.repository(
        component_name.github_organisation(),
        component_name.github_repo(),
    )

    url = repo._build_url('tarball', ref, base_url=repo._api)
    res = repo._get(url, verify=False, allow_redirects=True, stream=True)
    if not res.ok:
        raise RuntimeError(
            f'request to download github zip archive from {url=}'
            f' failed with {res.status_code=} {res.reason=}'
        )

    for chunk in res.iter_content(chunk_size=512):
        dest.write(chunk)

    dest.flush()
    dest.seek(0)
