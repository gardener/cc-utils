# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

    components = tuple(comp_descriptor.components())
    overwrites = tuple(comp_descriptor.component_overwrites())

    assert len(components) == 1
    component = components[0]

    assert len(overwrites) == 1
    overwrite = overwrites[0]

    declaring_comp = overwrite.declaring_component()

    assert declaring_comp == product.model.ComponentReference.create(
        name='example.org/acme/declaring',
        version='0.1.0-declaring'
    )

    dep_overwrites = tuple(overwrite.dependency_overwrites())

    assert len(dep_overwrites) == 1
    dep_overwrite = dep_overwrites[0]

    refd_component = dep_overwrite.references()
    assert refd_component == component

    overwrite_images = tuple(dep_overwrite.container_images())
    assert len(overwrite_images) == 1

    overwrite_img = overwrite_images[0]

    assert overwrite_img.name() == 'image_1'
    assert overwrite_img.version() == '1.2.3-patched'
    assert overwrite_img.image_reference() == 'alpine-patched:1.2.3-patched'


def test_implicit_overwrite_creation(minimal_component_descriptor_with_overwrites):
    comp_descriptor = minimal_component_descriptor_with_overwrites
    # patch-out overwrite (add again later)
    comp_descriptor.raw['component_overwrites'] = []

    overwrites = tuple(comp_descriptor.component_overwrites())
    assert len(overwrites) == 0

    component_name = product.model.Component.create(name='x.org/foo/bar', version='1.2.3')

    overwrite = comp_descriptor.component_overwrite(declaring_component=component_name)

    overwrites = tuple(comp_descriptor.component_overwrites())
    assert len(overwrites) == 1

    # no implicit creation for same declaring component version
    same_overwrite = comp_descriptor.component_overwrite(declaring_component=component_name)

    overwrites = tuple(comp_descriptor.component_overwrites())
    assert len(overwrites) == 1

    assert overwrite == same_overwrite

    dep_overwrite = overwrite.dependency_overwrite(
        referenced_component=component_name,
        create_if_absent=True,
    )
    dep_overwrite.add_container_image_overwrite(container_image=product.model.ContainerImage.create(
        name='dontcare',
        version='1.2.3',
        image_reference='i:1',
    ))

    # ensure it's propagated to component_descriptor
    comp_descriptor = product.model.ComponentDescriptor.from_dict(comp_descriptor.raw)
    overwrite = next(comp_descriptor.component_overwrites())
    dep_overwrt = overwrite.dependency_overwrite(referenced_component=component_name)
    assert len(tuple(dep_overwrt.container_images())) == 1
