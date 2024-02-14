# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import model.aws as examinee
from model.base import ModelValidationError


@pytest.fixture
def required_dict():
    return {
        'region': 'foo_region',
        'access_key_id': 'foo_accesskey',
        'secret_access_key': 'foo_secretkey',
    }


def test_validation_fails_on_missing_required_key(required_dict):
    for key in required_dict.keys():
        test_dict = required_dict.copy()
        test_dict.pop(key)
        element = examinee.AwsProfile(
            name='foo',
            raw_dict=test_dict,
            type_name='aws',
        )
        with pytest.raises(ModelValidationError):
            element.validate()


def test_validation_succeeds_on_required_dict(required_dict):
    element = examinee.AwsProfile(
        name='foo',
        raw_dict=required_dict,
        type_name='aws',
    )
    element.validate()


def test_validation_succeeds_on_unknown_key(required_dict):
    test_dict = {**required_dict, **{'foo': 'bar'}}
    element = examinee.AwsProfile(
        name='foo',
        raw_dict=test_dict,
        type_name='aws',
    )
    element.validate()
