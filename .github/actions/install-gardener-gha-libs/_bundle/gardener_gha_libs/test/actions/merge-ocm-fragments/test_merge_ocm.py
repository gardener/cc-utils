# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import tempfile

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
            'merge-ocm-fragments',
        )
    ),
)

import yaml

import merge_ocm


def _base_descriptor(version='1.0.0'):
    return {
        'component': {
            'name': 'example.com/my-component',
            'version': version,
        }
    }


def _write_fragment(fragments_dir, fname, resources=None, sources=None):
    fragment = {}
    if resources:
        fragment['resources'] = resources
    if sources:
        fragment['sources'] = sources
    with open(os.path.join(fragments_dir, fname), 'w') as f:
        yaml.safe_dump(fragment, f)


def test_merge_fragments_resources_merged():
    descriptor = _base_descriptor()
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', resources=[{'name': 'img-a', 'version': '1.0'}])
        _write_fragment(d, 'b.ocm-artefacts', resources=[{'name': 'img-b', 'version': '2.0'}])

        merge_ocm.merge_fragments(descriptor, d)

    names = [r['name'] for r in descriptor['component']['resources']]
    assert 'img-a' in names
    assert 'img-b' in names


def test_merge_fragments_sources_merged():
    descriptor = _base_descriptor()
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', sources=[{'name': 'src-a'}])

        merge_ocm.merge_fragments(descriptor, d)

    assert descriptor['component']['sources'][0]['name'] == 'src-a'


def test_merge_fragments_consumes_fragment_files():
    descriptor = _base_descriptor()
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', resources=[{'name': 'r'}])

        merge_ocm.merge_fragments(descriptor, d)

        assert not os.path.exists(os.path.join(d, 'a.ocm-artefacts'))


def test_merge_fragments_patches_local_version():
    descriptor = _base_descriptor(version='2.3.4')
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', resources=[{'name': 'r', 'relation': 'local'}])

        merge_ocm.merge_fragments(descriptor, d)

    resource = descriptor['component']['resources'][0]
    assert resource['version'] == '2.3.4'


def test_merge_fragments_existing_version_not_overwritten():
    descriptor = _base_descriptor(version='2.3.4')
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(
            d, 'a.ocm-artefacts',
            resources=[{'name': 'r', 'relation': 'local', 'version': '1.0.0'}],
        )

        merge_ocm.merge_fragments(descriptor, d)

    resource = descriptor['component']['resources'][0]
    assert resource['version'] == '1.0.0'


def test_merge_fragments_ignores_non_fragment_files():
    descriptor = _base_descriptor()
    with tempfile.TemporaryDirectory() as d:
        # write a non-fragment file — should not be touched
        other = os.path.join(d, 'component-descriptor.yaml')
        with open(other, 'w') as f:
            f.write('untouched')

        merge_ocm.merge_fragments(descriptor, d)

        assert os.path.exists(other)

    assert descriptor['component']['resources'] == []


def _write_meta(fragments_dir, fragment_fname, run_attempt):
    meta = {'run-attempt': run_attempt}
    with open(os.path.join(fragments_dir, f'{fragment_fname}.meta'), 'w') as f:
        yaml.safe_dump(meta, f)


def test_merge_fragments_deduplicates_same_attempt():
    '''Two fragments with identical identity and same attempt: first one wins, no duplicate.'''
    descriptor = _base_descriptor()
    resource = {'name': 'img', 'version': '1.0', 'data': 'first'}
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', resources=[resource])
        _write_meta(d, 'a.ocm-artefacts', run_attempt=2)
        _write_fragment(d, 'b.ocm-artefacts', resources=[{**resource, 'data': 'second'}])
        _write_meta(d, 'b.ocm-artefacts', run_attempt=2)

        merge_ocm.merge_fragments(descriptor, d)

    assert len(descriptor['component']['resources']) == 1


def test_merge_fragments_higher_attempt_wins():
    '''Same identity from two attempts: higher attempt replaces lower.'''
    descriptor = _base_descriptor()
    identity = {'name': 'img', 'version': '1.0', 'extraIdentity': {'architecture': 'arm64'}}
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', resources=[{**identity, 'access': 'old'}])
        _write_meta(d, 'a.ocm-artefacts', run_attempt=1)
        _write_fragment(d, 'b.ocm-artefacts', resources=[{**identity, 'access': 'new'}])
        _write_meta(d, 'b.ocm-artefacts', run_attempt=2)

        merge_ocm.merge_fragments(descriptor, d)

    resources = descriptor['component']['resources']
    assert len(resources) == 1
    assert resources[0]['access'] == 'new'


def test_merge_fragments_lower_attempt_does_not_overwrite():
    '''Fragments processed in arbitrary order: lower attempt must not overwrite higher.'''
    descriptor = _base_descriptor()
    identity = {'name': 'img', 'version': '1.0'}
    with tempfile.TemporaryDirectory() as d:
        # write higher attempt first so it gets processed first (alphabetically)
        _write_fragment(d, 'a.ocm-artefacts', resources=[{**identity, 'access': 'new'}])
        _write_meta(d, 'a.ocm-artefacts', run_attempt=3)
        _write_fragment(d, 'b.ocm-artefacts', resources=[{**identity, 'access': 'old'}])
        _write_meta(d, 'b.ocm-artefacts', run_attempt=1)

        merge_ocm.merge_fragments(descriptor, d)

    resources = descriptor['component']['resources']
    assert len(resources) == 1
    assert resources[0]['access'] == 'new'


def test_merge_fragments_no_meta_treated_as_attempt_zero():
    '''Fragment without sidecar (old format) gets attempt=0, overridden by any attempt>=1.'''
    descriptor = _base_descriptor()
    identity = {'name': 'img', 'version': '1.0'}
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', resources=[{**identity, 'access': 'old'}])
        # no .meta sidecar → attempt 0
        _write_fragment(d, 'b.ocm-artefacts', resources=[{**identity, 'access': 'new'}])
        _write_meta(d, 'b.ocm-artefacts', run_attempt=1)

        merge_ocm.merge_fragments(descriptor, d)

    resources = descriptor['component']['resources']
    assert len(resources) == 1
    assert resources[0]['access'] == 'new'


def test_merge_fragments_meta_sidecar_consumed():
    '''The .meta sidecar is deleted alongside its fragment.'''
    descriptor = _base_descriptor()
    with tempfile.TemporaryDirectory() as d:
        _write_fragment(d, 'a.ocm-artefacts', resources=[{'name': 'r', 'version': '1.0'}])
        _write_meta(d, 'a.ocm-artefacts', run_attempt=1)

        merge_ocm.merge_fragments(descriptor, d)

        assert not os.path.exists(os.path.join(d, 'a.ocm-artefacts'))
        assert not os.path.exists(os.path.join(d, 'a.ocm-artefacts.meta'))
