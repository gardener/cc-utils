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

import dacite
import pytest

import concourse.steps.images as images
import gci.componentmodel

examinee = images.image_reference_filter


@pytest.fixture
def resource():
    def _resource(
        image_reference,
        name='abc',
        version='1.2.3',
    ):
        return dacite.from_dict(
            data_class=gci.componentmodel.Resource,
            data={
                'name': name,
                'version': version,
                'type': 'ociImage',
                'access': {
                    'type': 'ociRegistry',
                    'imageReference': image_reference,
                },
            },
            config=dacite.Config(
                cast=[
                    gci.componentmodel.ResourceType,
                    gci.componentmodel.AccessType,
                ],
            ),
        )
    return _resource


def test_image_reference_filter(resource):
    res1 = resource('image1:bar')
    res2 = resource('image2/foo:bar')
    res3 = resource('another_image1:bar')
    res4 = resource('unrelated/for/testing:bar')

    default_filter = examinee()

    assert default_filter(res1) # by default, nothing should be filtered out
    assert default_filter(res2)
    assert default_filter(res3)
    assert default_filter(res4)

    # include only images starting with 'image'
    include_filter = examinee(include_regexes=('image.*',))

    assert include_filter(res1)
    assert include_filter(res2)
    assert not include_filter(res3)
    assert not include_filter(res4)

    # exclude images containing the string 'image'
    exclude_filter = examinee(exclude_regexes=('.*image.*',))

    assert not exclude_filter(res1)
    assert not exclude_filter(res2)
    assert not exclude_filter(res3)
    assert exclude_filter(res4)

    # exclude should have precedency over include
    exclude_with_predence = examinee(include_regexes=('.*image.*',), exclude_regexes=('.*2.*',))

    assert exclude_with_predence(res1)
    assert not exclude_with_predence(res2)
    assert exclude_with_predence(res3)
    assert not exclude_with_predence(res4)
