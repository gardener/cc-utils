# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import tempfile
import unittest.mock

import pytest
import yaml

# test/__init__.py already adds repo_root to sys.path;
# add the action directory so helm is importable
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'helmchart')
    ),
)

import ocm
import ocm.helm

import helm


def test_patch_values_yaml():
    resource = ocm.Resource(
        name='myimage',
        version='1.0',
        type=ocm.ArtefactType.OCI_IMAGE,
        access=ocm.OciAccess(
            imageReference='my.registry/repo/img:2.0',
        ),
    )
    component = ocm.Component(
        name='example.com/comp',
        version='1.0',
        repositoryContexts=[],
        provider='acme',
        resources=[resource],
        sources=[],
        componentReferences=[],
    )
    mapping = helm.HelmchartValueMapping(
        ref='ocm-resource:myimage.repository',
        attribute='image.repository',
    )

    tmpdir = tempfile.mkdtemp()
    values_path = os.path.join(tmpdir, 'values.yaml')
    with open(values_path, 'w') as f:
        f.write('image:\n  repository: original\n')

    with unittest.mock.patch('ocm.helm.find_resource', return_value=resource):
        helm.patch_values_yaml(
            component=component,
            values_yaml_path=values_path,
            mappings=[mapping],
        )

    with open(values_path) as f:
        patched = yaml.safe_load(f)

    assert patched['image']['repository'] == 'my.registry/repo/img'


def test_patch_values_yaml_missing_file():
    component = ocm.Component(
        name='example.com/comp',
        version='1.0',
        repositoryContexts=[],
        provider='acme',
        resources=[],
        sources=[],
        componentReferences=[],
    )
    with pytest.raises(FileNotFoundError):
        helm.patch_values_yaml(
            component=component,
            values_yaml_path='/nonexistent/path/values.yaml',
            mappings=[],
        )


def test_to_ocm_mapping():
    mappings = [
        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-1.repository',
            attribute='example.attribute.repo-1',
        ),
        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-1.tag',
            attribute='example.attribute.tag-1',
        ),
        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-1.image',
            attribute='example.attribute.image-1',
        ),

        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-2.repository',
            attribute='example.repo-2',
        ),

        helm.HelmchartValueMapping(
            ref='ocm-resource:resource-2.repository',
            attribute='sub.example.repo-2',
        ),

        helm.HelmchartValueMapping(
            ref='ocm-component-resource:github.com/component-1.resource-3.repository',
            attribute='example.attribute.repo-3',
        ),
    ]

    ocm_mapping = helm.to_ocm_mapping(
        helmchart_name='helmchart-name-1',
        mappings=mappings,
    )

    assert ocm_mapping == {
        'helmchartResource': {
            'name': 'helmchart-name-1',
        },
        'imageMapping': [
            {
                'resource': {
                    'name': 'resource-1',
                },
                'repository': 'example.attribute.repo-1',
                'tag': 'example.attribute.tag-1',
                'image': 'example.attribute.image-1',
            },
            {
                'resource': {
                    'name': 'resource-2',
                },
                'repository': 'example.repo-2',
            },
            {
                'resource': {
                    'name': 'resource-2',
                },
                'repository': 'sub.example.repo-2',
            },
            {
                'component': {
                    'name': 'github.com/component-1',
                },
                'resource': {
                    'name': 'resource-3',
                },
                'repository': 'example.attribute.repo-3',
            },
        ],
    }
