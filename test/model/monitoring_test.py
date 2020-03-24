# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import model.monitoring as examinee
from model.base import ModelValidationError


@pytest.fixture
def monitoring_required_dict():
    return {
            'namespace': 'foo',
            'kube_state_metrics': 'foo',
            'postgresql_exporter': 'foo',
            'tls_secret_name': 'foo',
            'basic_auth_secret_name': 'foo',
            'ingress_host': 'foo',
            'external_url': 'foo',
            'basic_auth_user': 'foo',
            'basic_auth_pwd': 'foo',
    }


def test_validation_fails_missing_required_key(monitoring_required_dict):
    for key in monitoring_required_dict.keys():
        test_dict = monitoring_required_dict.copy()
        test_dict.pop(key)
        element = examinee.CCMonitoringConfig(name='foo', raw_dict=test_dict)
        with pytest.raises(ModelValidationError):
            element.validate()


def test_monitoring_validation_succeeds_on_required_dict(monitoring_required_dict):
    element = examinee.CCMonitoringConfig(name='foo', raw_dict=monitoring_required_dict)
    element.validate()
