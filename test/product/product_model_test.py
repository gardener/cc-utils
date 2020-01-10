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

from copy import deepcopy
import random

import unittest

from _test_utils import AssertMixin

import product.model
import product.util

from model.base import ModelValidationError


class ProductModelTest(unittest.TestCase):
    def setUp(self):
        self.raw_dict = {
            'components':
            [
                # first_component
                {
                    'name': 'example.org/foo/first_component',
                    'version': 'first_version',
                    'dependencies':
                    {
                        'components':
                        [
                            {
                                'name': 'example.org/bar/second_component',
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
                    'name': 'example.org/bar/second_component',
                    'version': 'second_version',
                    'dependencies': None # no dependencies
                }
            ]
        }

    def test_deserialisation_returns_correct_model(self):
        examinee = product.model.ComponentDescriptor.from_dict(raw_dict=self.raw_dict)

        components = list(examinee.components())
        self.assertEqual(len(components), 2)

        first_component = examinee.component(('example.org/foo/first_component', 'first_version'))
        second_component = examinee.component(('example.org/bar/second_component', 'second_version'))

        self.assertEqual(first_component.name(), 'example.org/foo/first_component')
        self.assertEqual(second_component.name(), 'example.org/bar/second_component')

        first_dependencies = first_component.dependencies()
        second_dependencies = second_component.dependencies()

        first_component_deps = list(first_dependencies.components())
        self.assertEqual(len(first_component_deps), 1)
        first_component_dep = first_component_deps[0]

        first_container_deps = list(first_dependencies.container_images())
        self.assertEqual(len(first_container_deps), 1)
        first_container_dep = first_container_deps[0]

        self.assertEqual(first_component_dep.name(), 'example.org/bar/second_component')
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
        left_model = product.model.ComponentDescriptor.from_dict(raw_dict=self.raw_dict)
        right_model = product.model.ComponentDescriptor.from_dict(raw_dict=self.raw_dict)

        merged = product.util.merge_products(left_model, right_model)

        components = list(merged.components())
        self.assertEqual(len(components), 2)

    def test_merge_conflicting_products_should_raise(self):
        left_model = product.model.ComponentDescriptor.from_dict(raw_dict=deepcopy(self.raw_dict))
        right_model = product.model.ComponentDescriptor.from_dict(raw_dict=deepcopy(self.raw_dict))

        # add a new dependency to create a conflicting definition
        container_image_dep = product.model.ContainerImage.create(
                name='container_name',
                version='container_version',
                image_reference='dontcare:sometag',
        )
        first_comp_deps = right_model.component((
                    'example.org/foo/first_component',
                    'first_version')
                ).dependencies()
        first_comp_deps.add_container_image_dependency(container_image_dep)

        with self.assertRaises(ValueError):
            product.util.merge_products(left_model, right_model)

    def test_merge_products(self):
        left_model = product.model.ComponentDescriptor.from_dict(raw_dict={})
        right_model = product.model.ComponentDescriptor.from_dict(raw_dict={})

        left_component1 = product.model.Component.create(name='x/y/lcomp1', version='1')
        right_component1 = product.model.Component.create(name='x/y/rcomp1', version='2')

        left_model.add_component(left_component1)
        right_model.add_component(right_component1)

        merged = product.util.merge_products(left_model, right_model)

        merged_components = list(merged.components())
        self.assertEqual(len(merged_components), 2)

        self.assertIsNotNone(merged.component(('x/y/lcomp1', '1')))
        self.assertIsNotNone(merged.component(('x/y/rcomp1', '2')))


def test_merge_products_with_overwrites():
    left_model = product.model.ComponentDescriptor.from_dict(raw_dict={})
    right_model = product.model.ComponentDescriptor.from_dict(raw_dict={})

    left_component1 = product.model.Component.create(name='x/y/lc1', version='1')
    left_component2 = product.model.Component.create(name='x/y/lc2', version='1')
    image1 = product.model.ContainerImage.create(
        name='image1',
        version='1.2.3',
        image_reference='image:1',
    )

    left_comp_overwrites = left_model.component_overwrite(declaring_component=left_component1)
    left_dep_overwrites = left_comp_overwrites.dependency_overwrite(
        referenced_component=left_component2,
        create_if_absent=True,
    )
    left_dep_overwrites.add_container_image_overwrite(image1)

    merged = product.util.merge_products(left_model, right_model)

    merged_comp_overwrites = merged.component_overwrite(declaring_component=left_component1)
    merged_dep_overwrites = merged_comp_overwrites.dependency_overwrite(
        referenced_component=left_component2,
        create_if_absent=False,
    )

    overwritten_image = merged_dep_overwrites.container_image(name='image1', version='1.2.3')

    assert overwritten_image == image1

    # test merging in inverse order
    merged = product.util.merge_products(right_model, left_model)

    merged_comp_overwrites = merged.component_overwrite(declaring_component=left_component1)
    merged_dep_overwrites = merged_comp_overwrites.dependency_overwrite(
        referenced_component=left_component2,
        create_if_absent=False,
    )

    overwritten_image = merged_dep_overwrites.container_image(name='image1', version='1.2.3')

    assert overwritten_image == image1


class ComponentModelTest(unittest.TestCase, AssertMixin):
    def test_create(self):
        examinee = product.model.Component.create(name='github.com/example/name', version='1.2.3')

        self.assertEqual(examinee.name(), 'github.com/example/name')
        self.assertEqual(examinee.version(), '1.2.3')

    def test_add_dependencies(self):
        examinee = product.model.Component.create(name='github.com/example/name', version='1.2.3')
        deps = examinee.dependencies()
        self.assertEmpty(deps.components())

        component_dep = product.model.ComponentReference.create(
                name='github.com/foo/bar',
                version='2'
        )

        deps.add_component_dependency(component_dep)

        self.assertEqual(tuple(deps.components()), (component_dep,))


class ComponentReferenceModelTest(unittest.TestCase):
    def test_component_name_parsing(self):
        examinee = product.model.ComponentReference.create(name='github.com/org/rname', version='1')

        self.assertEqual(examinee.github_host(), 'github.com')
        self.assertEqual(examinee.github_organisation(), 'org')
        self.assertEqual(examinee.github_repo(), 'rname')


class ComponentNameModelTest(unittest.TestCase):
    def test_validate_component_name(self):
        examinee = product.model.ComponentName.validate_component_name

        invalid_component_names = (
            '',
            'http://github.com/example/example',
            'github.com',
            'github.com/',
            'github.com/foo',
            'github.com/foo/',
            'github.com/foo/bar/x',
        )

        for component_name in invalid_component_names:
            with self.assertRaises(product.model.InvalidComponentReferenceError):
                examinee(component_name)

        # test valid names
        examinee('github.com/example/example')
        examinee('github.com/example/example/')

    def test_from_github_repo_url(self):
        examinee = product.model.ComponentName.from_github_repo_url

        result1 = examinee('https://github.xxx/foo_org/bar_name')
        result2 = examinee('github.xxx/foo_org/bar_name')

        for result in (result1, result2):
            self.assertEqual(result.github_repo(), 'bar_name')
            self.assertEqual(result.github_organisation(), 'foo_org')
            self.assertEqual(result.github_host(), 'github.xxx')
            self.assertEqual(result.github_repo_path(), 'foo_org/bar_name')
            self.assertEqual(result.config_name(), 'github_xxx')


class DependenciesModelTest(unittest.TestCase, AssertMixin):
    def test_ctor(self):
        examinee = product.model.ComponentDependencies(raw_dict={})

        self.assertEmpty(examinee.web_dependencies())
        self.assertEmpty(examinee.generic_dependencies())
        self.assertEmpty(examinee.container_images())
        self.assertEmpty(examinee.components())

    def test_adding_dependencies(self):
        examinee = product.model.ComponentDependencies(raw_dict={})

        ci_dep = product.model.ContainerImage.create(
            name='cn',
            version='cv',
            image_reference='cir:ct',
        )
        comp_dep = product.model.ComponentReference.create(name='h/o/c', version='c')
        web_dep = product.model.WebDependency.create(name='wn', version='wv', url='u')
        gen_dep = product.model.GenericDependency.create(name='gn', version='gv')

        examinee.add_container_image_dependency(ci_dep)
        self.assertEqual((ci_dep,), tuple(examinee.container_images()))

        examinee.add_component_dependency(comp_dep)
        self.assertEqual((comp_dep,), tuple(examinee.components()))

        examinee.add_web_dependency(web_dep)
        self.assertEqual((web_dep,), tuple(examinee.web_dependencies()))

        examinee.add_generic_dependency(gen_dep)
        self.assertEqual((gen_dep,), tuple(examinee.generic_dependencies()))

        # adding the same dependency multiple times must be ignored
        redundant_dep = product.model.GenericDependency.create(name='gn', version='gv')
        self.assertEqual(redundant_dep, gen_dep)
        examinee.add_generic_dependency(redundant_dep)
        self.assertEqual((gen_dep,), tuple(examinee.generic_dependencies()))


class ContainerImageTest(unittest.TestCase):
    def test_validation(self):
        examinee = product.model.ContainerImage.create

        # image references must contain a ':'
        with self.assertRaises(ModelValidationError):
            examinee(name='made_up', version='some_version', image_reference='foo')

        # image_references must contain a tag
        with self.assertRaises(ModelValidationError):
            examinee(name='made_up', version='some_version', image_reference=':bar')

        # image references must contain an image name
        with self.assertRaises(ModelValidationError):
            examinee(name='made_up', version='some_version', image_reference='foo:')


def test_version():
    V = product.model.Version

    v1 = V('1.2.3')
    v2 = V('2.2.2')
    v3 = V('v0.1.2')
    v4 = V('v0.5')
    v5 = V('4.0')
    v6 = V('foo') # not valid semver, but also allowed

    # test sorting
    all_versions = [v1, v2, v3, v4, v5, v6]

    for _ in range(5):
        random.shuffle(all_versions)
        # do this multiple times in order to catch order-dependent errors
        assert sorted(all_versions) == [v3, v4, v1, v2, v5, v6]

    # test equality
    assert V('a') == V('a')
    assert V('a') != V('b')
    assert V('a') != V('1.2.3')
    assert V('1.2.3') == v1
    assert V('v1.2.3') == v1
    assert v1 != v2
    assert v1 != 42
