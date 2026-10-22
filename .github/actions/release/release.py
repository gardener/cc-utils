import collections.abc
import dataclasses
import enum
import hashlib
import os

import dacite
import yaml

import ocm
import release_notes.ocm as rn_ocm


@dataclasses.dataclass(kw_only=True)
class Asset:
    '''
    Model-Class for deserialising entries from `inputs.asset` input.
    '''
    name: str
    mime_type: str | None = None
    type: str
    id: dict[str, str]

    def matches(self, resource: ocm.ConstructorResource):
        # special-handling for version, name, and type (as those are defined on toplevel)
        for k,v in self.id.items():
            if k in ('name', 'version', 'type'):
                resource_value = getattr(resource, k)
            else:
                resource_value = (resource.extraIdentity or {}).get(k)

            if isinstance(resource_value, enum.Enum):
                resource_value = resource_value.value

            if not resource_value == v:
                return False

        # reject if resource carries extraIdentity keys not covered by id
        extra = resource.extraIdentity or {}
        return not (extra.keys() - self.id.keys())

    def __post_init__(self):
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
        assets = [assets]

    for asset in assets:
        asset['mime_type'] = asset.pop('mime-type', None)

        yield dacite.from_dict(
            data_class=Asset,
            data=asset,
        )


def find_blob(
    blobs_dir: str,
    asset: Asset,
    component: ocm.ComponentConstructor,
) -> tuple[str, str]:
    '''
    lookup OCM-resource selected by given `asset`. The resource is assumed to have either
    an input of type File/v1 (OCM v2 constructor format) or a localBlob access (legacy format),
    with the blob being expected to reside below `blobs_dir`.

    returns both the found path, and the mediaType as a two-tuple.
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
        pass

    if resource.input:
        alg_and_hexdigest = resource.input.path
        media_type = resource.input.mediaType
    elif resource.access and hasattr(resource.access, 'localReference'):
        alg_and_hexdigest = resource.access.localReference
        media_type = resource.access.mediaType
    else:
        print(f'Error: {resource=} has neither input nor localBlob access')
        exit(1)

    path = os.path.join(blobs_dir, alg_and_hexdigest)

    if not os.path.isfile(path):
        print(f'Error: {path=} does not exist (but was referenced by {resource=} / {asset=}')
        exit(1)

    return path, media_type


def _write_blob(blobs_dir: str, octets: bytes) -> str:
    '''Write blob to blobs_dir and return its sha256 digest name.'''
    digest = f'sha256:{hashlib.sha256(octets).hexdigest()}'
    path = os.path.join(blobs_dir, digest)
    with open(path, 'wb') as f:
        f.write(octets)
    return digest


def attach_release_notes(
    component: ocm.ComponentConstructor,
    release_notes_markdown: str,
    tar_bytes: bytes,
    blobs_dir: str,
) -> None:
    '''
    Write release-notes blobs to blobs_dir and append the corresponding resources
    with input entries to the component constructor.
    '''
    if release_notes_markdown:
        octets = release_notes_markdown.encode('utf-8')
        digest = _write_blob(blobs_dir, octets)
        component.resources.append(
            ocm.ConstructorResource(
                name=rn_ocm.release_notes_resource_name_old,
                version=component.version,
                type='text/markdown.release-notes',
                relation=ocm.ResourceRelation.LOCAL,
                input=ocm.FileInput(
                    path=digest,
                    mediaType='text/markdown.release-notes',
                ),
            ),
        )

    tar_digest = _write_blob(blobs_dir, tar_bytes)
    component.resources.append(
        ocm.ConstructorResource(
            name=rn_ocm.release_notes_resource_name,
            version=component.version,
            type='application/tar.release-notes',
            relation=ocm.ResourceRelation.LOCAL,
            input=ocm.FileInput(
                path=tar_digest,
                mediaType='application/tar.release-notes',
            ),
        ),
    )


def attach_branch_info(
    component: ocm.ComponentConstructor,
    branch_info_bytes: bytes,
    blobs_dir: str,
) -> None:
    '''
    Write branch-info blob to blobs_dir and append the corresponding resource
    with an input entry to the component constructor.
    '''
    digest = _write_blob(blobs_dir, branch_info_bytes)
    component.resources.append(
        ocm.ConstructorResource(
            name='branch-info',
            version=component.version,
            type='application/vnd.gardener.cloud.branch-info+yaml',
            relation=ocm.ResourceRelation.LOCAL,
            input=ocm.FileInput(
                path=digest,
                mediaType='application/vnd.gardener.cloud.branch-info+yaml',
            ),
        ),
    )
