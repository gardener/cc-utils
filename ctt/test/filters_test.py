# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import pytest

import gci.componentmodel as cm

import ctt.filters as filters


@pytest.fixture
def img(name='image_name', version='1.2.3', ref='image_ref:1.2.3'):
    def _img(name=name, version=version, ref=ref):
        return cm.Resource(
            name=name,
            version=version,
            type=cm.ResourceType.OCI_IMAGE,
            access=cm.OciAccess(
                imageReference=ref,
            )
        )
    return _img


@pytest.fixture
def comp(name='a.b/c/e', version='1.2.3'):
    def _comp(name=name, version=version):
        return cm.ComponentReference(
            name=name,
            componentName=name,
            version=version,
        )
    return _comp


def test_image_filter(img):
    examinee = filters.ImageFilter(
        include_image_refs=('image:1',),
        exclude_image_refs=('image:2', 'image3'),
        include_image_names=('in1', 'in2'),
        exclude_image_names=('in3',),
    )

    image1 = img(ref='image:1', name='in1')

    assert examinee.matches(component=None, resource=image1)

    image2 = img(ref='image:1', name='another_name')

    assert not examinee.matches(component=None, resource=image2)


def test_component_filter(comp):
    examinee = filters.ComponentFilter(
        include_component_names=('x.o/f/c1', 'c2',),
        exclude_component_names=('x.y/z/c3',),
    )

    comp1 = comp(name='x.o/f/c1')

    assert examinee.matches(component=comp1, resource=None)

    comp2 = comp(name='x.y/z/c3')

    assert not examinee.matches(component=comp2, resource=None)
