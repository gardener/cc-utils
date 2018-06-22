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
from copy import deepcopy

import unittest

import product.model
import product.util

class ProductModelTest(unittest.TestCase):
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
                                'name': 'first',
                                'version': 'version',
                                'image_reference': 'first_creference:version',
                            }
                        ],
                        'web':
                        [
                            {
                                'name': 'first_web',
                                'version': 'web_version',
                                'url': 'https://example.org',
                            },
                        ],
                        'generic':
                        [
                            {
                                'name': 'generic',
                                'version': 'generic_version',
                            },
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
        examinee = product.model.Product.from_dict(raw_dict=self.raw_dict)

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

        self.assertEqual(first_container_dep.image_reference(), 'first_creference:version')

        self.assertEqual(len(list(second_dependencies.components())), 0)
        self.assertEqual(len(list(second_dependencies.container_images())), 0)

        first_web_deps = list(first_dependencies.web_dependencies())
        self.assertEqual(len(first_web_deps), 1)
        first_web_dep = first_web_deps[0]

        self.assertEqual(first_web_dep.name(), 'first_web')
        self.assertEqual(first_web_dep.version(), 'web_version')
        self.assertEqual(first_web_dep.url(), 'https://example.org')

        first_generic_deps = list(first_dependencies.generic_dependencies())
        self.assertEqual(len(first_generic_deps), 1)
        first_generic_dep = first_generic_deps[0]

        self.assertEqual(first_generic_dep.name(), 'generic')
        self.assertEqual(first_generic_dep.version(), 'generic_version')

    def test_merge_identical_products(self):
        left_model = product.model.Product.from_dict(raw_dict=self.raw_dict)
        right_model = product.model.Product.from_dict(raw_dict=self.raw_dict)

        merged = product.util.merge_products(left_model, right_model)

        components = list(merged.components())
        self.assertEquals(len(components), 2)

    def test_merge_conflicting_products_should_raise(self):
        left_model = product.model.Product.from_dict(raw_dict=deepcopy(self.raw_dict))
        right_model = product.model.Product.from_dict(raw_dict=deepcopy(self.raw_dict))

        # add a new dependency to create a conflicting definition
        container_image_dep = product.model.ContainerImage.create(
                name='container_name',
                version='container_version',
                image_reference='dontcare',
        )
        first_comp_deps = right_model.component(('first_component', 'first_version')).dependencies()
        first_comp_deps.add_container_image_dependency(container_image_dep)

        with self.assertRaises(ValueError):
            product.util.merge_products(left_model, right_model)

    def test_merge_products(self):
        left_model = product.model.Product.from_dict(raw_dict={})
        right_model = product.model.Product.from_dict(raw_dict={})

        left_component1 = product.model.Component.create(name='lcomp1', version='1')
        right_component1 = product.model.Component.create(name='rcomp1', version='2')

        left_model.add_component(left_component1)
        right_model.add_component(right_component1)

        merged = product.util.merge_products(left_model, right_model)
        print(merged.raw)

        merged_components = list(merged.components())
        self.assertEqual(len(merged_components), 2)

        self.assertIsNotNone(merged.component(('lcomp1', '1')))
        self.assertIsNotNone(merged.component(('rcomp1', '2')))

