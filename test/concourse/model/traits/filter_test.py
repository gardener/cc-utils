import dataclasses

import pytest

import concourse.model.traits.filter as examinee


# Dummy dataclass for testing.
# We could also test using dicts, but since the actual objects the filter will be applied to
# will usually be dataclasses, use this simple dataclass instead.
# We could of course also test using the actual gci.componentmodel classes, but that would expose
# the test to changes in the model.
@dataclasses.dataclass
class Dummy:
    name: str


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
    component_dummy = Dummy(name='TestComponent')
    with pytest.raises(ValueError):
        test_filter(component_dummy, None)


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

    component_dummy = Dummy(name='TestComponent')
    assert test_filter(component_dummy, None)

    component_dummy = Dummy(name='AnotherName')
    assert not test_filter(component_dummy, None)


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

    component_dummy = Dummy(name='AnotherName')
    assert test_filter(component_dummy, None)

    component_dummy = Dummy(name='TestComponent')
    assert not test_filter(component_dummy, None)


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

    resource_dummy = Dummy(name='TestResource')
    assert test_filter(None, resource_dummy)

    resource_dummy = Dummy(name='AnotherName')
    assert not test_filter(None, resource_dummy)


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

    resource_dummy = Dummy(name='AnotherName')
    assert test_filter(None, resource_dummy)

    resource_dummy = Dummy(name='TestResource')
    assert not test_filter(None, resource_dummy)


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

    component_dummy = Dummy(name='AName')
    assert not test_filter(component_dummy, None)

    component_dummy = Dummy(name='AnotherName')
    assert not test_filter(component_dummy, None)

    component_dummy = Dummy(name='YetAnotherName')
    assert not test_filter(component_dummy, None)


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

    resource_dummy = Dummy(name='AName')
    assert not test_filter(None, resource_dummy)

    resource_dummy = Dummy(name='AnotherName')
    assert not test_filter(None, resource_dummy)

    resource_dummy = Dummy(name='YetAnotherName')
    assert not test_filter(None, resource_dummy)


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

    matching_component_dummy = Dummy(name='ComponentName')
    matching_resource_dummy = Dummy(name='ResourceName')

    assert test_filter(matching_component_dummy, matching_resource_dummy)

    resource_dummy = Dummy(name='AnotherResource')
    assert test_filter(matching_component_dummy, resource_dummy)

    component_dummy = Dummy(name='AnotherComponent')
    assert test_filter(component_dummy, matching_resource_dummy)

    assert not test_filter(component_dummy, resource_dummy)
