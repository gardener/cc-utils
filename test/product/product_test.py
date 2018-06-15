# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import unittest

import product.model

class ProductDeserialisationTest(unittest.TestCase):
    def setUp(self):
        self.raw_dict = {
            'components':
            [
                # first_component
                {
                    'name': 'first_component',
                    'version': 'first_version',
                    'dependencies':
                    {
                        'components':
                        [
                            {
                                'name': 'second_component',
                                'version': 'second_version',
                            }
                        ],
                        'container_images':
                        [
                            {
                                'image_reference': 'first_creference',
                            }
                        ],
                    },
                },
                # second_component
                {
                    'name': 'second_component',
                    'version': 'second_version',
                    'dependencies': None # no dependencies
                }
            ]
        }

    def test_deserialisation_returns_correct_model(self):
        examinee = product.model.Product.from_dict(name='product_name', raw_dict=self.raw_dict)

        components = list(examinee.components())
        self.assertEquals(len(components), 2)

        first_component = examinee.component(('first_component', 'first_version'))
        second_component = examinee.component(('second_component', 'second_version'))

        self.assertEquals(first_component.name(), 'first_component')
        self.assertEquals(second_component.name(), 'second_component')

        first_dependencies = first_component.dependencies()
        second_dependencies = second_component.dependencies()

        first_component_deps = list(first_dependencies.components())
        self.assertEqual(len(first_component_deps), 1)
        first_component_dep = first_component_deps[0]

        first_container_deps = list(first_dependencies.container_images())
        self.assertEqual(len(first_container_deps), 1)
        first_container_dep = first_container_deps[0]

        self.assertEqual(first_component_dep.name(), 'second_component')
        self.assertEqual(first_component_dep.version(), 'second_version')

        self.assertEqual(first_container_dep.image_reference(), 'first_creference')

        self.assertEqual(len(list(second_dependencies.components())), 0)
        self.assertEqual(len(list(second_dependencies.container_images())), 0)


