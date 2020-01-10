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

import concourse.steps.images as images
import product.model

examinee = images.image_reference_filter


@pytest.fixture
def img_ref():
    def _img_ref(image_reference, name='abc', version='1.2.3'):
        return product.model.ContainerImage.create(
                name=name,
                version=version,
                image_reference=image_reference,
        )
    return _img_ref


def test_image_reference_filter(img_ref):
    ref1 = img_ref('image1:bar')
    ref2 = img_ref('image2/foo:bar')
    ref3 = img_ref('another_image1:bar')
    ref4 = img_ref('unrelated/for/testing:bar')

    default_filter = examinee()

    assert default_filter(ref1) # by default, nothing should be filtered out
    assert default_filter(ref2)
    assert default_filter(ref3)
    assert default_filter(ref4)

    # include only images starting with 'image'
    include_filter = examinee(include_regexes=('image.*',))

    assert include_filter(ref1)
    assert include_filter(ref2)
    assert not include_filter(ref3)
    assert not include_filter(ref4)

    # exclude images containing the string 'image'
    exclude_filter = examinee(exclude_regexes=('.*image.*',))

    assert not exclude_filter(ref1)
    assert not exclude_filter(ref2)
    assert not exclude_filter(ref3)
    assert exclude_filter(ref4)

    # exclude should have precedency over include
    exclude_with_predence = examinee(include_regexes=('.*image.*',), exclude_regexes=('.*2.*',))

    assert exclude_with_predence(ref1)
    assert not exclude_with_predence(ref2)
    assert exclude_with_predence(ref3)
    assert not exclude_with_predence(ref4)
