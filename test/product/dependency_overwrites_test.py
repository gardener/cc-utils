import product.model


def test_implicit_comp_overwrites_creation():
    component_overwrites = product.model.ComponentOverwrites.create(
        declaring_component=product.model.Component.create(name='x.org/foo/bar', version='1.2.3'),
    )

    assert len(tuple(component_overwrites.dependency_overwrites())) == 0

    # implicitly create overwrite
    component = product.model.Component.create(name='x.org/bar/foo', version='2.3.4')

    dependency_overwrites = component_overwrites.dependency_overwrite(referenced_component=component)

    assert len(tuple(component_overwrites.dependency_overwrites())) == 1

    # ensure only one overwrite per component
    same_overwrites = component_overwrites.dependency_overwrite(referenced_component=component)

    assert len(tuple(component_overwrites.dependency_overwrites())) == 1
    assert dependency_overwrites == same_overwrites
