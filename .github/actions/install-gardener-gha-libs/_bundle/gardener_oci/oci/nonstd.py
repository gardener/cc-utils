'''
Functions that go beyond the OCI Distribution Specification.

Registry-vendor APIs are used where necessary; not all registry types are
supported. Pass `raise_if_unsupported=False` to silently skip unsupported
registries instead of raising.

Currently supported: Keppel, Google Artifact Registry (GAR), AWS ECR, Azure ACR.
'''
import typing
import urllib.parse

import oci.auth as oa
import oci.client as oc
import oci.model as om


def iter_repositories(
    client: oc.Client,
    image_reference: str | om.OciImageReference,
    registry_type: om.OciRegistryType = None,
    raise_if_unsupported: bool = True,
) -> typing.Iterator[str]:
    '''
    Iterate over all repository names within the registry (or account, for
    registries with multi-tenancy) identified by `image_reference`.

    `image_reference` may be a bare registry prefix (e.g.
    ``keppel.eu-de-1.acme.org/my-account``) or any fully-qualified image
    reference hosted on the target registry.

    If `registry_type` is not given it is guessed from `image_reference`.
    When the registry type is unsupported and `raise_if_unsupported` is True
    (the default), a ``ValueError`` is raised. If `raise_if_unsupported` is
    False an empty iterator is returned instead.

    Paging is handled transparently; callers receive a flat stream of names.

    Currently supported registry types: Keppel, GAR, AWS ECR, Azure ACR.
    '''
    image_reference = om.OciImageReference.to_image_ref(image_reference)
    if registry_type is None:
        registry_type = image_reference.registry_type

    if registry_type is om.OciRegistryType.KEPPEL:
        return _iter_repositories_keppel(client, image_reference)
    if registry_type is om.OciRegistryType.GAR:
        return _iter_repositories_gar(client, image_reference)
    if registry_type in (om.OciRegistryType.AWS, om.OciRegistryType.AZURE):
        return _iter_repositories_catalog(client, image_reference)

    if raise_if_unsupported:
        raise ValueError(
            f'iter_repositories: unsupported registry type {registry_type!r} '
            f'(derived from {image_reference!r})'
        )
    return iter(())


def _iter_repositories_keppel(
    client: oc.Client,
    image_reference: om.OciImageReference,
) -> typing.Iterator[str]:
    host = image_reference.netloc
    # account is the first path segment
    account = image_reference.urlparsed.path.lstrip('/').split('/')[0]
    scope = f'keppel_account:{account}:view'

    marker = None
    while True:
        path = f'accounts/{account}/repositories'
        if marker:
            path += f'?marker={urllib.parse.quote(marker)}'
        url = f'https://{host}/keppel/v1/{path}'

        res = client._request(url=url, image_reference=image_reference, scope=scope)
        data = res.json()

        repos = data.get('repositories', [])
        for repo in repos:
            yield repo['name']

        if not data.get('truncated') or not repos:
            break
        marker = repos[-1]['name']


def _iter_repositories_gar(
    client: oc.Client,
    image_reference: om.OciImageReference,
) -> typing.Iterator[str]:
    host = image_reference.netloc
    # host format: {location}-docker.pkg.dev
    location = host.removesuffix('-docker.pkg.dev')
    path_parts = image_reference.urlparsed.path.lstrip('/').split('/')
    if len(path_parts) < 2:
        raise ValueError(
            f'GAR image reference must include project and repository: {image_reference!r}'
        )
    project = path_parts[0]
    repository = path_parts[1]

    # for GAR, credentials_lookup yields username='oauth2accesstoken', password=<google_access_token>
    creds = client.credentials_lookup(
        image_reference=str(image_reference),
        privileges=oa.Privileges.READONLY,
        absent_ok=False,
    )
    access_token = creds.password

    base_url = (
        f'https://artifactregistry.googleapis.com/v1'
        f'/projects/{project}/locations/{location}'
        f'/repositories/{repository}/packages'
    )
    page_token = None
    while True:
        params = {'pageToken': page_token} if page_token else {}
        throttle = client._throttle_for('artifactregistry.googleapis.com')
        with throttle:
            res = client.session.get(
                base_url,
                headers={'Authorization': f'Bearer {access_token}'},
                params=params,
            )
        res.raise_for_status()
        data = res.json()

        for pkg in data.get('packages', []):
            # pkg['name'] = 'projects/.../repositories/.../packages/<name>'
            # <name> may be URL-encoded if it contains slashes
            yield urllib.parse.unquote(pkg['name'].split('/packages/', 1)[1])

        page_token = data.get('nextPageToken')
        if not page_token:
            break


def _parse_catalog_next_url(link_header: str) -> str | None:
    for part in link_header.split(','):
        url_part, *params = part.strip().split(';')
        if any('rel="next"' in p for p in params):
            return url_part.strip().strip('<>')
    return None


def _iter_repositories_catalog(
    client: oc.Client,
    image_reference: om.OciImageReference,
    page_size: int = 100,
) -> typing.Iterator[str]:
    # standard Docker Distribution V2 catalog endpoint; supported by ECR and Azure ACR
    host = image_reference.netloc
    scope = 'registry:catalog:*'
    url = f'https://{host}/v2/_catalog?n={page_size}'

    while url:
        res = client._request(url=url, image_reference=image_reference, scope=scope)
        yield from res.json().get('repositories', [])
        url = _parse_catalog_next_url(res.headers.get('Link', ''))
