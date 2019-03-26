# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
        element = examinee.AwsProfile(name='foo', raw_dict=test_dict)
        with pytest.raises(ModelValidationError):
            element.validate()


def test_validation_succeeds_on_required_dict(required_dict):
    element = examinee.AwsProfile(name='foo', raw_dict=required_dict)
    element.validate()


def test_validation_succeeds_on_unknown_key(required_dict):
    test_dict = {**required_dict, **{'foo': 'bar'}}
    element = examinee.AwsProfile(name='foo', raw_dict=test_dict)
    element.validate()
