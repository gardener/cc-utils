# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import pytest

import model.base as examinee
from model.base import ModelValidationError


class ModelBaseTest:
    def test_raw_dict_values_are_stored(self):
        empty_dict = dict()
        model_base = examinee.ModelBase(raw_dict=empty_dict)

        self.assertIs(model_base.raw, empty_dict)


class BasicCredentialsTest:
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
