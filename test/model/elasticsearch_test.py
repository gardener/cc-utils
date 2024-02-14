# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import model.elasticsearch as examinee
from model.base import ModelValidationError


@pytest.fixture
def required_dict():
    return {
        'endpoint_url': 'foo',
        'endpoints': 'foo',
    }


def test_validation_fails_on_missing_required_key(required_dict):
    for key in required_dict.keys():
        test_dict = required_dict.copy()
        test_dict.pop(key)
        element = examinee.ElasticSearchConfig(
            name='foo',
            raw_dict=test_dict,
            type_name='elasticsearch',
        )
        with pytest.raises(ModelValidationError):
            element.validate()


def test_validation_succeeds_on_required_dict(required_dict):
    element = examinee.ElasticSearchConfig(
        name='foo',
        raw_dict=required_dict,
        type_name='elasticsearch',
    )
    element.validate()


def test_validation_fails_on_unknown_key(required_dict):
    # since optional attributes are defined for ElasticSearchConfig, test should fail
    test_dict = {**required_dict, **{'foo': 'bar'}}
    element = examinee.ElasticSearchConfig(
        name='foo',
        raw_dict=test_dict,
        type_name='elasticsearch',
    )
    with pytest.raises(ModelValidationError):
        element.validate()
