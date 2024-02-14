# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import model.github as examinee
from model.base import ModelValidationError


@pytest.fixture
def required_dict():
    return {
            'sshUrl': 'foo',
            'httpUrl': 'foo',
            'apiUrl': 'foo',
            'disable_tls_validation': 'foo',
            'available_protocols': ['https', 'ssh'],
            'technical_users': [{
                'username': 'foo',
                'password': 'foo',
                'emailAddress': 'foo',
                'privateKey': 'foo',
                'authToken': 'foo',
            }]
    }


def test_validation_fails_on_missing_required_key(required_dict):
    for key in required_dict.keys():
        test_dict = required_dict.copy()
        test_dict.pop(key)
        element = examinee.GithubConfig(
            name='foo',
            raw_dict=test_dict,
            type_name='github',
        )
        with pytest.raises(ModelValidationError):
            element.validate()


def test_validation_succeeds_on_required_dict(required_dict):
    element = examinee.GithubConfig(
        name='foo',
        raw_dict=required_dict,
        type_name='github',
    )
    element.validate()
