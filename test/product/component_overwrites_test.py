# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest

import product.model


@pytest.fixture
def minimal_component_descriptor_with_overwrites():
    return product.model.ComponentDescriptor.from_dict({
        'components': [{
            'name': 'example.org/foo/bar',
            'version': '1.2.3',
            'dependencies': {
                'container_images': [
                    {
                        'name': 'image_1',
                        'version': '1.2.3',
                        'image_reference': 'alpine:1.2.3',
                    }
                ],
            }
        }],
        'component_overwrites': [
            {
                'declaring_component': {
                    'name': 'example.org/acme/declaring',
                    'version': '0.1.0-declaring',
                },
                'dependency_overwrites': [{
                    'references': {
                        'name': 'example.org/foo/bar',
                        'version': '1.2.3',
                    },
                    'container_images': [
                        {
                            'name': 'image_1',
                            'version': '1.2.3-patched',
                            'image_reference': 'alpine-patched:1.2.3-patched',
                        }
                    ],
                }],
            }
        ],
    })


def test_overwrites_parsing(minimal_component_descriptor_with_overwrites):
    comp_descriptor = minimal_component_descriptor_with_overwrites

    overwrites = tuple(comp_descriptor.component_overwrites())

    assert len(overwrites) == 1
    overwrite = overwrites[0]

    declaring_comp = overwrite.declaring_component()

    assert declaring_comp == product.model.ComponentReference.create(
        name='example.org/acme/declaring',
        version='0.1.0-declaring'
    )
