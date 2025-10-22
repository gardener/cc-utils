import copy
import pytest

import ocm
import ocm.iter
import ocm.validate

valid_ocm_component_descriptor = ocm.ComponentDescriptor(
    component=ocm.Component(
        name='acme.org/my-component',
        version='1.2.3',
        repositoryContexts=[],
        provider='ACME Inc.',
        sources=[],
        componentReferences=[],
        resources=[],
        labels=[],
        creationTime=None,
    ),
    meta=ocm.Metadata(),
)


def test_iter_results():
    nodes = tuple(
        ocm.iter.iter(
            component=valid_ocm_component_descriptor,
            lookup=None,
            recursion_depth=0,
        )
    )

    # we expect no result if all validations are skipped
    assert len(
        tuple(
            ocm.validate.iter_results(
                nodes=nodes,
                validation_cfg=ocm.validate.ValidationCfg(
                    schema=ocm.validate.ValidationMode.SKIP,
                    access=ocm.validate.ValidationMode.SKIP,
                    artefact_uniqueness=ocm.validate.ValidationMode.SKIP,
                )
            )
        )
    ) == 0

    for result in ocm.validate.iter_results(
        nodes=nodes,
        validation_cfg=ocm.validate.ValidationCfg(
            schema=ocm.validate.ValidationMode.FAIL,
            access=ocm.validate.ValidationMode.SKIP,
            artefact_uniqueness=ocm.validate.ValidationMode.FAIL,
        )
    ):
        assert result.ok
        assert result.passed is True
        assert not isinstance(result, ocm.validate.ValidationError)

    # now let's make component-descriptor _invalid_

    invalid_component = copy.deepcopy(valid_ocm_component_descriptor).component
    invalid_component.name = 'invalid-cname'

    nodes = tuple(
        ocm.iter.iter(
            component=invalid_component,
            lookup=None,
            recursion_depth=0,
        )
    )

    # we expect exactly one error (invalid component-name)
    errors = []

    for result in ocm.validate.iter_violations(
        nodes=nodes,
        validation_cfg=ocm.validate.ValidationCfg(
            schema=ocm.validate.ValidationMode.FAIL,
            access=ocm.validate.ValidationMode.SKIP,
            artefact_uniqueness=ocm.validate.ValidationMode.FAIL,
        ),
        oci_client=None,
    ):
        if result.ok:
            pytest.fail('iter-violations must not return `ok`-results')
        errors.append(result)

    assert len(errors) == 1
    error, = errors

    assert isinstance(error, ocm.validate.ValidationError)
    assert error.passed is False
    assert 'does not match' in error.error

    # let's add another error (duplicate resource)
    invalid_component.resources.append(
        ocm.Resource(
            name='r1',
            version='1.2.3',
            type='t',
            access={'type': 1},
            extraIdentity={},
        )
    )
    invalid_component.resources.append(
        ocm.Resource(
            name='r1',
            version='1.2.3',
            type='t',
            access={'type': 2}, # access should not be considered for artefact-identity
            extraIdentity={},
        )
    )

    nodes = tuple(
        ocm.iter.iter(
            component=invalid_component,
            lookup=None,
            recursion_depth=0,
        )
    )

    # we expect exactly two errors (invalid component-name and duplicate resource)
    errors = []

    for result in ocm.validate.iter_results(
        nodes=nodes,
        validation_cfg=ocm.validate.ValidationCfg(
            schema=ocm.validate.ValidationMode.FAIL,
            access=ocm.validate.ValidationMode.SKIP,
            artefact_uniqueness=ocm.validate.ValidationMode.FAIL,
        )
    ):
        if result.ok:
            continue
        errors.append(result)

    assert len(errors) == 2
    for error in errors:
        if error.type is ocm.validate.ValidationType.ARTEFACT_UNIQUENESS:
            break
    else:
        pytest.fail('did not find ValidationError with type artefact-uniqueness')
