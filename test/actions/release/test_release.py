# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import hashlib
import os
import sys
import tempfile

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions', 'release')
    ),
)

import release
import release_notes.ocm as rn_ocm
import ocm


def _make_cd_dict(version='1.0.0', resources=None):
    return {
        'version': version,
        'resources': resources if resources is not None else [],
    }


# --- attach_release_notes_to_dict ---

def test_markdown_blob_written_when_non_empty():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()
    markdown = 'some release notes'

    release.attach_release_notes_to_dict(cd_dict, markdown, b'tar', blobs_dir)

    octets = markdown.encode('utf-8')
    expected_digest = f'sha256:{hashlib.sha256(octets).hexdigest()}'
    assert os.path.isfile(os.path.join(blobs_dir, expected_digest))


def test_markdown_blob_skipped_when_empty():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()

    release.attach_release_notes_to_dict(cd_dict, '', b'tar', blobs_dir)

    # only the tar blob should be written
    blobs = [f for f in os.listdir(blobs_dir) if not os.path.islink(os.path.join(blobs_dir, f))]
    assert len(blobs) == 1


def test_tar_blob_always_written():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()
    tar_bytes = b'tar data'

    release.attach_release_notes_to_dict(cd_dict, '', tar_bytes, blobs_dir)

    expected_digest = f'sha256:{hashlib.sha256(tar_bytes).hexdigest()}'
    assert os.path.isfile(os.path.join(blobs_dir, expected_digest))


def test_release_notes_resources_appended_with_markdown():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()

    release.attach_release_notes_to_dict(cd_dict, 'notes', b'tar', blobs_dir)

    names = [r['name'] for r in cd_dict['resources']]
    assert rn_ocm.release_notes_resource_name_old in names
    assert rn_ocm.release_notes_resource_name in names


def test_release_notes_resources_appended_without_markdown():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()

    release.attach_release_notes_to_dict(cd_dict, '', b'tar', blobs_dir)

    names = [r['name'] for r in cd_dict['resources']]
    assert rn_ocm.release_notes_resource_name_old not in names
    assert rn_ocm.release_notes_resource_name in names


def test_release_notes_resources_use_file_input():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()

    release.attach_release_notes_to_dict(cd_dict, 'notes', b'tar', blobs_dir)

    for r in cd_dict['resources']:
        assert 'input' in r
        assert 'access' not in r
        assert r['input']['type'] == str(ocm.InputType.FILE)
        assert r['input']['path'].startswith('sha256:')


# --- attach_branch_info_to_dict ---

def test_branch_info_blob_written():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()
    data = b'branch: main\n'

    release.attach_branch_info_to_dict(cd_dict, data, blobs_dir)

    expected_digest = f'sha256:{hashlib.sha256(data).hexdigest()}'
    assert os.path.isfile(os.path.join(blobs_dir, expected_digest))


def test_branch_info_resource_appended():
    blobs_dir = tempfile.mkdtemp()
    cd_dict = _make_cd_dict()

    release.attach_branch_info_to_dict(cd_dict, b'data', blobs_dir)

    resources = cd_dict['resources']
    assert len(resources) == 1
    resource = resources[0]
    assert resource['name'] == 'branch-info'
    assert resource['version'] == '1.0.0'
    assert 'input' in resource
    assert 'access' not in resource


# --- Asset.matches() ---

def _make_asset(name, extra_id):
    '''helper: build an Asset with the given name and extra extraIdentity fields'''
    return release.Asset(
        name=f'{name}-linux-amd64',
        mime_type=None,
        type='ocm-resource',
        id={'name': name, 'os': 'linux', 'architecture': 'amd64', **extra_id},
    )


def _make_resource(name, extra_identity, type='executable'):
    return ocm.Resource(
        name=name,
        version='1.0.0',
        type=type,
        extraIdentity=extra_identity,
        access=ocm.LocalBlobAccess(
            localReference='sha256:abc',
            mediaType='application/octet-stream',
        ),
        relation=ocm.ResourceRelation.LOCAL,
    )


def test_asset_matches_exact_extra_identity():
    asset = _make_asset('gardenadm', {})
    resource = _make_resource('gardenadm', {'os': 'linux', 'architecture': 'amd64'})
    assert asset.matches(resource)


def test_asset_no_match_wrong_name():
    asset = _make_asset('gardenadm', {})
    resource = _make_resource('other', {'os': 'linux', 'architecture': 'amd64'})
    assert not asset.matches(resource)


def test_asset_no_match_wrong_arch():
    asset = _make_asset('gardenadm', {})
    resource = _make_resource('gardenadm', {'os': 'linux', 'architecture': 'arm64'})
    assert not asset.matches(resource)


def test_asset_no_match_sbom_resource_with_extra_key():
    # SBOM resource shares name/os/arch but adds sbom-format — must NOT match
    asset = _make_asset('gardenadm', {})
    sbom_resource = _make_resource(
        'gardenadm',
        {'os': 'linux', 'architecture': 'amd64', 'sbom-format': 'spdx-2.3'},
        type='application/spdx+json',
    )
    assert not asset.matches(sbom_resource)


def test_asset_matches_sbom_resource_when_format_specified():
    # caller can still select the SBOM resource explicitly by including sbom-format in id
    asset = release.Asset(
        name='gardenadm-linux-amd64-spdx',
        mime_type=None,
        type='ocm-resource',
        id={'name': 'gardenadm', 'os': 'linux', 'architecture': 'amd64', 'sbom-format': 'spdx-2.3'},
    )
    sbom_resource = _make_resource(
        'gardenadm',
        {'os': 'linux', 'architecture': 'amd64', 'sbom-format': 'spdx-2.3'},
        type='application/spdx+json',
    )
    assert asset.matches(sbom_resource)


def test_gardener_gardenadm_ambiguity():
    # regression test: gardener/gardener Release workflow failed with
    # "asset=Asset(..., id={'name': 'gardenadm', 'os': 'linux', 'architecture': 'amd64'})
    #  is ambiguous (more than one matching OCM-Resource)"
    # because SBOM inline resources share name/os/arch with the binary resource.
    asset = release.Asset(
        name='gardenadm-linux-amd64',
        mime_type=None,
        type='ocm-resource',
        id={'name': 'gardenadm', 'os': 'linux', 'architecture': 'amd64'},
    )
    binary = _make_resource('gardenadm', {'os': 'linux', 'architecture': 'amd64'})
    spdx = _make_resource(
        'gardenadm',
        {'os': 'linux', 'architecture': 'amd64', 'sbom-format': 'spdx-2.3'},
        type='application/spdx+json',
    )
    cdx = _make_resource(
        'gardenadm',
        {'os': 'linux', 'architecture': 'amd64', 'sbom-format': 'cyclonedx-1.6'},
        type='application/vnd.cyclonedx+json',
    )

    matches = [r for r in (binary, spdx, cdx) if asset.matches(r)]
    assert matches == [binary]


def test_asset_type_in_id_matches():
    # type is a top-level field checked via getattr; enum values must be normalised
    asset = release.Asset(
        name='img',
        mime_type=None,
        type='ocm-resource',
        id={'name': 'img', 'type': 'ociImage'},
    )
    resource = ocm.Resource(
        name='img',
        version='1.0.0',
        type=ocm.ArtefactType.OCI_IMAGE,  # enum — value is 'ociImage'
        extraIdentity={},
        access=ocm.LocalBlobAccess(
            localReference='sha256:abc',
            mediaType='application/octet-stream',
        ),
        relation=ocm.ResourceRelation.LOCAL,
    )
    assert asset.matches(resource)


def test_asset_type_in_id_no_match():
    asset = release.Asset(
        name='img',
        mime_type=None,
        type='ocm-resource',
        id={'name': 'img', 'type': 'blob/v1'},
    )
    resource = ocm.Resource(
        name='img',
        version='1.0.0',
        type=ocm.ArtefactType.OCI_IMAGE,
        extraIdentity={},
        access=ocm.LocalBlobAccess(
            localReference='sha256:abc',
            mediaType='application/octet-stream',
        ),
        relation=ocm.ResourceRelation.LOCAL,
    )
    assert not asset.matches(resource)


def test_asset_version_in_id_matches():
    asset = release.Asset(
        name='bin',
        mime_type=None,
        type='ocm-resource',
        id={'name': 'bin', 'version': '1.0.0'},
    )
    assert asset.matches(_make_resource('bin', {}))


def test_asset_version_in_id_no_match():
    asset = release.Asset(
        name='bin',
        mime_type=None,
        type='ocm-resource',
        id={'name': 'bin', 'version': '9.9.9'},
    )
    assert not asset.matches(_make_resource('bin', {}))


def test_asset_version_in_extra_identity_covered_by_id():
    # reviewer concern: version appears in both id and resource extraIdentity;
    # self.id.keys() covers it so the extra-key check must not block the match
    asset = release.Asset(
        name='bin',
        mime_type=None,
        type='ocm-resource',
        id={'name': 'bin', 'version': '1.0.0', 'os': 'linux'},
    )
    resource = _make_resource('bin', {'version': '1.0.0', 'os': 'linux'})
    assert asset.matches(resource)


def test_asset_no_match_resource_missing_required_extra_key():
    # id requires 'os' but resource extraIdentity has no such key
    asset = _make_asset('bin', {})
    resource = _make_resource('bin', {'architecture': 'amd64'})  # 'os' absent
    assert not asset.matches(resource)
