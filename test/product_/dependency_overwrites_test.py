import product.model

comp = product.model.Component.create(name='x.org/foo/bar', version='1.2.3')


def test_implicit_comp_overwrites_creation():
    component_overwrites = product.model.ComponentOverwrites.create(
        declaring_component=comp,
    )

    assert len(tuple(component_overwrites.dependency_overwrites())) == 0

    # implicitly create overwrite
    component = product.model.Component.create(name='x.org/bar/foo', version='2.3.4')

    dependency_overwrites = component_overwrites.dependency_overwrite(
        referenced_component=component,
        create_if_absent=True,
    )

    assert len(tuple(component_overwrites.dependency_overwrites())) == 1

    # ensure only one overwrite per component
    same_overwrites = component_overwrites.dependency_overwrite(
        referenced_component=component,
        create_if_absent=True,
    )

    assert len(tuple(component_overwrites.dependency_overwrites())) == 1
    assert dependency_overwrites == same_overwrites


def test_adding_image_overwrites():
    dependency_overwrites = product.model.DependencyOverwrites.create(referenced_component=comp)

    assert len(tuple(dependency_overwrites.container_images())) == 0
    assert dependency_overwrites.references() == comp

    image = product.model.ContainerImage.create(name='c1', version='1.2.3', image_reference='r:1')

    dependency_overwrites.add_container_image_overwrite(container_image=image)

    image_overwrites = tuple(dependency_overwrites.container_images())

    assert len(image_overwrites) == 1
    assert image_overwrites[0] == image

    # test deduplication
    dependency_overwrites.add_container_image_overwrite(container_image=image)

    assert len(tuple(dependency_overwrites.container_images())) == 1
