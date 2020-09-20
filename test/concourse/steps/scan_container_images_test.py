# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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
