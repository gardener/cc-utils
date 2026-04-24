# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys

# test/__init__.py already adds repo_root to sys.path;
# add the action directory so helm is importable
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions',
                     'helmchart')
    ),
)

import helm


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
