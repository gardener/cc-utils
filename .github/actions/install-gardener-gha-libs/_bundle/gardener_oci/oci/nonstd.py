'''
Functions that go beyond the OCI Distribution Specification.

Registry-vendor APIs are used where necessary; not all registry types are
supported. Pass `raise_if_unsupported=False` to silently skip unsupported
registries instead of raising.

Currently supported: Keppel.
'''
import typing
import urllib.parse

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
    '''
    image_reference = om.OciImageReference.to_image_ref(image_reference)
    if registry_type is None:
        registry_type = image_reference.registry_type

    if registry_type is om.OciRegistryType.KEPPEL:
        return _iter_repositories_keppel(client, image_reference)

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
