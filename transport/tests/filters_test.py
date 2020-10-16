import processing.filters as filters


def test_image_filter(oci_img):
    examinee = filters.ImageFilter(
        include_image_refs=('image:1',),
        exclude_image_refs=('image:2', 'image3'),
        include_image_names=('in1','in2'),
        exclude_image_names=('in3',),
    )

    image1 = oci_img(ref='image:1', name='in1')

    assert examinee.matches(component=None, container_image=image1)

    image2 = oci_img(ref='image:1', name='another_name')

    assert not examinee.matches(component=None, container_image=image2)


def test_component_filter(comp):
    examinee = filters.ComponentFilter(
        include_component_names=('x.o/f/c1', 'c2',),
        exclude_component_names=('x.y/z/c3',),
    )

    comp1 = comp(name='x.o/f/c1')

    assert examinee.matches(component=comp1, container_image=None)

    comp2 = comp(name='x.y/z/c3')

    assert not examinee.matches(component=comp2, container_image=None)
