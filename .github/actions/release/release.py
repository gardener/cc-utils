import collections.abc
import dataclasses
import enum
import hashlib
import os

import dacite
import yaml

import ocm
import release_notes.ocm as rn_ocm


def _write_blob(blobs_dir: str, data: bytes) -> str:
    '''Write data to blobs_dir/<sha256:hexdigest> and return the digest name.'''
    digest = f'sha256:{hashlib.sha256(data).hexdigest()}'
    os.makedirs(blobs_dir, exist_ok=True)
    path = os.path.join(blobs_dir, digest)
    if not os.path.exists(path):
        with open(path, 'wb') as f:
            f.write(data)
    return digest


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

        # reject if resource carries extraIdentity keys not covered by id —
        # such keys are part of the OCM identity and make it a distinct resource
        # (e.g. SBOM resources share name/os/arch with binaries but add sbom-format)
        return not (resource.extraIdentity.keys() - self.id.keys())

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


def attach_release_notes_to_dict(
    cd_dict: dict,
    release_notes_markdown: str,
    tar_bytes: bytes,
    blobs_dir: str,
) -> None:
    '''
    Write release-notes blobs to blobs_dir and append the corresponding resource
    dicts (with file inputs) to cd_dict['resources'].
    '''
    resources = cd_dict.setdefault('resources', [])

    version = cd_dict['version']

    if release_notes_markdown:
        octets = release_notes_markdown.encode('utf-8')
        digest = _write_blob(blobs_dir, octets)
        resources.append({
            'name': rn_ocm.release_notes_resource_name_old,
            'version': version,
            'type': 'text/markdown.release-notes',
            'relation': 'local',
            'input': {
                'type': str(ocm.InputType.FILE),
                'path': digest,
                'mediaType': 'text/markdown.release-notes',
            },
        })

    tar_digest = _write_blob(blobs_dir, tar_bytes)
    resources.append({
        'name': rn_ocm.release_notes_resource_name,
        'version': version,
        'type': 'application/tar.release-notes',
        'relation': 'local',
        'input': {
            'type': str(ocm.InputType.FILE),
            'path': tar_digest,
            'mediaType': 'application/tar.release-notes',
        },
    })


def attach_branch_info_to_dict(
    cd_dict: dict,
    branch_info_bytes: bytes,
    blobs_dir: str,
) -> None:
    '''
    Write branch-info blob to blobs_dir and append the corresponding resource
    dict (with file input) to cd_dict['resources'].
    '''
    version = cd_dict['version']
    digest = _write_blob(blobs_dir, branch_info_bytes)
    cd_dict.setdefault('resources', []).append({
        'name': 'branch-info',
        'version': version,
        'type': 'application/vnd.gardener.cloud.branch-info+yaml',
        'relation': 'local',
        'input': {
            'type': str(ocm.InputType.FILE),
            'path': digest,
            'mediaType': 'application/vnd.gardener.cloud.branch-info+yaml',
        },
    })
