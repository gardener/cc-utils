# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import model.container_registry as examinee
from model.base import ModelValidationError


@pytest.fixture
def required_dict():
    return {
        'username': 'foo',
        'password': 'foo',
    }


def test_validation_fails_on_missing_required_key(required_dict):
    for key in required_dict.keys():
        test_dict = required_dict.copy()
        test_dict.pop(key)
        element = examinee.ContainerRegistryConfig(
            name='foo',
            raw_dict=test_dict,
            type_name='container_registry',
        )
        with pytest.raises(ModelValidationError):
            element.validate()


def test_validation_succeeds_on_required_dict(required_dict):
    element = examinee.ContainerRegistryConfig(
        name='foo',
        raw_dict=required_dict,
        type_name='container_registry',
    )
    element.validate()


def test_validation_fails_on_unknown_key(required_dict):
    # since optional attributes are defined for ContainerRegistryConfig, test should fail
    test_dict = {**required_dict, **{'foo': 'bar'}}
    element = examinee.ContainerRegistryConfig(
        name='foo',
        raw_dict=test_dict,
        type_name='container_registry',
    )
    with pytest.raises(ModelValidationError):
        element.validate()
