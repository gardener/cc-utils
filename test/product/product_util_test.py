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

import unittest
import pytest

import product.util as util
import product.model as model

# functions under test
greatest_crefs = util.greatest_references
diff_components = util.diff_components


def component_ref(name, version, prefix='gh.com/o/'):
    return model.ComponentReference.create(name=prefix + name, version=version)


@pytest.fixture
def cref():
    return component_ref


class ProductUtilTest(unittest.TestCase):
    def setUp(self):
        self.cref1 = component_ref(name='c1', version='1.2.3')
        self.cref2 = component_ref(name='c2', version='2.2.3')

    def test_greatest_references(self):
        # trivial case: single cref
        result = list(greatest_crefs((self.cref1,)))
        self.assertSequenceEqual(result, (self.cref1,))

        # trivial case: two crefs, different
        result = list(greatest_crefs((self.cref2, self.cref1)))
        self.assertSequenceEqual(result, (self.cref2, self.cref1))

        # non-trivial case: duplicate component name, different versions
        cref1_greater = model.ComponentReference.create(name=self.cref1.name(), version='9.0.9')
        cref1_lesser = model.ComponentReference.create(name=self.cref1.name(), version='0.0.1')

        result = set(greatest_crefs((self.cref1, self.cref2, cref1_greater, cref1_lesser)))
        self.assertSetEqual({cref1_greater, self.cref2}, result)

    def test_greatest_references_argument_validation(self):
        # None-check
        with self.assertRaises(ValueError):
            next(greatest_crefs(None))

        # reject elements that are not component references
        with self.assertRaises(ValueError):
            non_component_element = 42 # int is not of type product.model.ComponentReference
            next(greatest_crefs((self.cref1, non_component_element)))


def test_diff_components(cref):
    left_components = (
        cref('c1', '1.2.3'),
        cref('c2', '1.2.3'),
        cref('c3', '1.2.3'),
    )
    right_components = (
        cref('c1', '2.2.3'), # version changed
        cref('c2', '1.2.3'), # no change
        #cref('c3', '1.2.3'), # missing on right
        cref('c4', '1.2.3'), # added on right
    )

    result = diff_components(left_components, right_components)

    assert result.crefs_only_left == {cref('c3', '1.2.3'), cref('c1', '1.2.3')}
    assert result.crefs_only_right == {cref('c4', '1.2.3'), cref('c1', '2.2.3')}
    assert result.crefpairs_version_changed == {(cref('c1', '1.2.3'), cref('c1', '2.2.3'))}
    assert result.names_only_left == {'gh.com/o/c3'}
    assert result.names_only_right == {'gh.com/o/c4'}
    assert result.names_version_changed == {'gh.com/o/c1'}


def test_enumerate_effective_images(cref):
    comp_desc = model.ComponentDescriptor.from_dict({})

    c1 = cref('c1', '1.2.3')
    comp_desc.add_component(c1)
    c1 = comp_desc.component(c1)

    c1deps = c1.dependencies()
    image1 = model.ContainerImage.create(name='i1', version='1.2.3', image_reference='i:1')
    # image2 shares same logical name (-> regression test)
    image2 = model.ContainerImage.create(name='i1', version='1.2.4', image_reference='i:2')
    images_count = 2

    c1deps.add_container_image_dependency(image1)
    c1deps.add_container_image_dependency(image2)

    # ensure it's contained in regular enumerate_images
    comps_and_images = tuple(util._enumerate_images(comp_desc))
    assert len(comps_and_images) == images_count
    result_c, result_i = comps_and_images[0]
    assert result_c == c1
    assert result_i == image1

    # ensure it's also there in enumerate_effective_images (with no overwrites)
    comps_and_images = tuple(util._enumerate_effective_images(comp_desc))
    assert len(comps_and_images) == images_count
    result_c, result_i = comps_and_images[0]
    assert result_c == c1
    assert result_i == image1

    # now add an overwrite
    comp_overwrites = comp_desc.component_overwrite(declaring_component=cref('dontcare1', '1.2.3'))
    dep_overwrites = comp_overwrites.dependency_overwrite(
        referenced_component=c1,
        create_if_absent=True,
    )
    # name and version must match
    image_overwrite = model.ContainerImage.create(name='i1', version='1.2.3', image_reference='i:2')
    dep_overwrites.add_container_image_overwrite(image_overwrite)

    # ensure the overwrite is evaluated
    comps_and_images = tuple(util._enumerate_effective_images(comp_desc))
    assert len(comps_and_images) == images_count
    result_c, result_i = comps_and_images[0]
    assert result_c == c1
    assert result_i == image_overwrite
