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

import model.base as examinee
from model.base import ModelValidationError


class ModelBaseTest(object):
    def test_raw_dict_values_are_stored(self):
        empty_dict = dict()
        model_base = examinee.ModelBase(raw_dict=empty_dict)

        self.assertIs(model_base.raw, empty_dict)


class BasicCredentialsTest(object):
    @pytest.fixture
    def credentials_dict(self):
        return {
            'username':'foo',
            'password':'bar',
        }

    def test_validation_fails_on_missing_key(self, credentials_dict):
        for key in credentials_dict.keys():
            with self.subTest(key=key):
                test_dict = credentials_dict.copy()
                test_dict.pop(key)
                element = examinee.BasicCredentials(test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()
