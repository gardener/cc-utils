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
