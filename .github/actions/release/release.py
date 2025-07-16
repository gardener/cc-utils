import collections.abc
import dataclasses
import enum
import os

import dacite
import yaml

import ocm


@dataclasses.dataclass(kw_only=True)
class Asset:
    '''
    Model-Class for deserialising entries from `inputs.asset` input.
    '''
    name: str
    mime_type: str | None = None
    type: str
    id: dict[str, str]

    def matches(self, resource: ocm.Resource):
        # special-handling for version, name, and type (as those are defined on toplevel)
        for k,v in self.id.items():
            if k in ('name', 'version', 'type'):
                resource_value = getattr(resource, k)
            else:
                resource_value = resource.extraIdentity.get(k)

            if isinstance(resource_value, enum.Enum):
                resource_value = resource_value.value

            if not resource_value == v:
                return False

        return True

    def __post_init__(self):
        # basic validation: id.name must be present and non-empty
        if not self.id.get('name'):
            print('Error: asset.id.name must not be empty:')
            print(self)
            exit(1)


def iter_assets(
    path: str,
) -> collections.abc.Iterable[Asset]:
    with open(path) as f:
        assets = yaml.safe_load(f)

    if not assets:
        return

    if isinstance(assets, dict):
        # it is okay-ish if user only gave us a single element
        assets = [assets]

    for asset in assets:
        # kebap -> camel
        asset['mime_type'] = asset.pop('mime-type', None)

        yield dacite.from_dict(
            data_class=Asset,
            data=asset,
        )


def find_blob(
    blobs_dir: str,
    asset: Asset,
    component: ocm.Component,
) -> tuple[str, ocm.LocalBlobAccess]:
    '''
    lookup OCM-resource selected by given `asset`. The resource is assume to have an access of
    type localBlob, with the blob being expected to reside below `blobs_dir`, as is the case
    after running `merge-ocm-fragments` action.

    returns both the found path, and access as a two-tuple (the latter contains mime-type, which
    makes it useful for uploading as a github-release-asset).
    '''
    matching_resources = (res for res in component.resources if asset.matches(res))

    try:
        resource = next(matching_resources)
    except StopIteration:
        print(f'Error: did not find matching ocm-resource for {asset=}')
        exit(1)

    try:
        next(matching_resources)
        print(f'Error: {asset=} is ambiguous (more than one matching OCM-Resource)')
        exit(1)
    except StopIteration:
        pass # okay, we _want_ to have only one match

    # for now, we only allow localBlobs
    access = resource.access
    if not access.type is ocm.AccessType.LOCAL_BLOB:
        print(f'Error: {resource=} has unsupported access-type (only localBlob is allowed)')
        exit(1)

    # format: sha256:<digest> - as output by `merge-ocm-fragments` action
    alg_and_hexdigest = access.localReference
    path = os.path.join(
        blobs_dir,
        alg_and_hexdigest,
    )

    if not os.path.isfile(path):
        print(f'Error: {path=} does not exist (but was referenced by {resource=} / {asset=}')
        exit(1)

    return path, access
