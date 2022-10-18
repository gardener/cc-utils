import pytest

import gci.componentmodel as cm

import concourse.model.traits.filter as examinee
import cnudie.iter


def component(name='TestComponent', version='1.2.3', resources=()):
    return cm.Component(
        name=name,
        version=version,
        repositoryContexts=(),
        provider=None,
        sources=resources,
        resources=(),
        componentReferences=(),
    )


def resource(name='resourceName', version='1.2.3'):
    return cm.Resource(
        name=name,
        version=version,
        type='some-type',
        access=None,
    )


def test_unspecific_target_fails():
    test_config = examinee.MatchingConfig(
        name='Some Config Name',
        rules=[
            examinee.ConfigRule(
                target='Name',
                expression='TestComponent',
                matching_semantics=examinee.ComponentFilterSemantics('include'),
            )
        ]
    )
    test_filter = examinee.filter_for_matching_config(test_config)

    node = cnudie.iter.ComponentNode(path=())
    with pytest.raises(ValueError):
        test_filter(node)


def test_component_attr_included():
    test_config = examinee.MatchingConfig(
        name='Some Config Name',
        rules=[
            examinee.ConfigRule(
                target='component.name',
                expression='TestComponent',
                matching_semantics=examinee.ComponentFilterSemantics('include'),
            )
        ]
    )
    test_filter = examinee.filter_for_matching_config(test_config)

    assert test_filter(
        cnudie.iter.ComponentNode(path=(
            component(),
        ))
    )

    assert not test_filter(
        cnudie.iter.ComponentNode(path=(
            component(name='unknown-component'),
        )
    ))


def test_component_attr_excluded():
    test_config = examinee.MatchingConfig(
        name='Some Config Name',
        rules=[
            examinee.ConfigRule(
                target='component.name',
                expression='TestComponent',
                matching_semantics=examinee.ComponentFilterSemantics('exclude'),
            )
        ]
    )
    test_filter = examinee.filter_for_matching_config(test_config)

    assert test_filter(
        cnudie.iter.ComponentNode(path=(
            component(name='excluded-component'),
        )
    ))

    assert not test_filter(
        cnudie.iter.ComponentNode(path=(
            component(name='TestComponent'),
        )
    ))


def test_resource_attr_included():
    test_config = examinee.MatchingConfig(
        name='Some Config Name',
        rules=[
            examinee.ConfigRule(
                target='resource.name',
                expression='TestResource',
                matching_semantics=examinee.ComponentFilterSemantics('include'),
            )
        ]
    )
    test_filter = examinee.filter_for_matching_config(test_config)

    assert test_filter(
        cnudie.iter.ResourceNode(
            path=(),
            resource=resource(name='TestResource'),
    ))

    assert not test_filter(
        cnudie.iter.ResourceNode(
            path=(),
            resource=resource(name='another-resource-name'),
    ))


def test_resource_attr_excluded():
    test_config = examinee.MatchingConfig(
        name='Some Config Name',
        rules=[
            examinee.ConfigRule(
                target='resource.name',
                expression='TestResource',
                matching_semantics=examinee.ComponentFilterSemantics('exclude'),
            )
        ]
    )
    test_filter = examinee.filter_for_matching_config(test_config)

    assert not test_filter(
        cnudie.iter.ResourceNode(
            path=(),
            resource=resource(name='TestResource'),
    ))

    assert test_filter(
        cnudie.iter.ResourceNode(
            path=(),
            resource=resource(name='another-resource-name'),
    ))


def test_multiple_component_rules():
    # rules are ANDed - expect no matches
    test_config = examinee.MatchingConfig(
        name='Some Config Name',
        rules=[
            examinee.ConfigRule(
                target='component.name',
                expression='AName',
                matching_semantics=examinee.ComponentFilterSemantics('include'),
            ),
            examinee.ConfigRule(
                target='component.name',
                expression='AnotherName',
                matching_semantics=examinee.ComponentFilterSemantics('include'),
            )
        ]
    )
    test_filter = examinee.filter_for_matching_config(test_config)

    assert not test_filter(
        cnudie.iter.ComponentNode(path=(
            component(name='AName'),
        )
    ))

    assert not test_filter(
        cnudie.iter.ComponentNode(path=(
            component(name='AnotherName'),
        )
    ))

    assert not test_filter(
        cnudie.iter.ComponentNode(path=(
            component(name='YetAnotherName'),
        )
    ))


def test_multiple_resource_rules():
    test_config = examinee.MatchingConfig(
        name='Some Config Name',
        rules=[
            examinee.ConfigRule(
                target='resource.name',
                expression='AName',
                matching_semantics=examinee.ComponentFilterSemantics('include'),
            ),
            examinee.ConfigRule(
                target='resource.name',
                expression='AnotherName',
                matching_semantics=examinee.ComponentFilterSemantics('include'),
            )
        ]
    )
    test_filter = examinee.filter_for_matching_config(test_config)

    assert not test_filter(
        cnudie.iter.ResourceNode(
            path=(),
            resource=resource(name='another-resource-name'),
    ))

    assert not test_filter(
        cnudie.iter.ResourceNode(
            path=(),
            resource=resource(name='AnotherName'),
    ))

    assert not test_filter(
        cnudie.iter.ResourceNode(
            path=(),
            resource=resource(name='YetAnotherName'),
    ))


def test_multiple_configs():
    # matching-configs are OR-ed
    test_configs = [
        examinee.MatchingConfig(
            name='Some Config Name',
            rules=[
                examinee.ConfigRule(
                    target='component.name',
                    expression='ComponentName',
                    matching_semantics=examinee.ComponentFilterSemantics('include'),
                ),
            ]
        ),
        examinee.MatchingConfig(
            name='Another Config Name',
            rules=[
                examinee.ConfigRule(
                    target='resource.name',
                    expression='ResourceName',
                    matching_semantics=examinee.ComponentFilterSemantics('include'),
                )
            ]
        ),
    ]
    test_filter = examinee.filter_for_matching_configs(test_configs)

    assert test_filter(
        cnudie.iter.ResourceNode(
            path=(
                component(name='ComponentName'),
            ),
            resource=resource(name='YetAnotherName'),
    ))

    assert test_filter(
        cnudie.iter.ResourceNode(
            path=(
                component(name='ComponentName'),
            ),
            resource=resource(name='AnotherResource'),
    ))

    assert test_filter(
        cnudie.iter.ResourceNode(
            path=(
                component(name='another-component'),
            ),
            resource=resource(name='ResourceName'),
    ))
