# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import pytest

import model.webhook_dispatcher as examinee
from model.base import ModelValidationError


class TestWebhookDispatcherConfig(object):
    @pytest.fixture
    def required_dict(self):
        return {
                'concourse_config_names': 'foo',
        }

    def test_validation_fails_on_missing_required_key(self, required_dict):
        for key in required_dict.keys():
            test_dict = required_dict.copy()
            test_dict.pop(key)
            element = examinee.WebhookDispatcherConfig(name='foo', raw_dict=test_dict)
            with pytest.raises(ModelValidationError):
                element.validate()

    def test_validation_succeeds_on_required_dict(self, required_dict):
        element = examinee.WebhookDispatcherConfig(name='foo', raw_dict=required_dict)
        element.validate()

    def test_validation_succeeds_on_unknown_key(self, required_dict):
        test_dict = {**required_dict, **{'foo': 'bar'}}
        element = examinee.WebhookDispatcherConfig(name='foo', raw_dict=test_dict)
        element.validate()


class TestWebhookDispatcherDeploymentConfig(object):
    @pytest.fixture
    def deployment_required_dict(self):
        return {
            'whd_image': {
                'image_name': 'foo',
                'image_tag': 'bar',
            },
            'ingress_host': 'foo',
            'ingress_config': 'foo',
            'external_url': 'foo',
            'secrets_server_config': 'foo',
            'kubernetes_config': 'foo',
            'webhook_dispatcher_config': 'foo',
            'container_port': 'foo',
            'logging_els_index': 'foo',
        }

    def test_validation_fails_on_missing_required_key(self, deployment_required_dict):
        for key in deployment_required_dict.keys():
            test_dict = deployment_required_dict.copy()
            test_dict.pop(key)
            element = examinee.WebhookDispatcherDeploymentConfig(name='foo', raw_dict=test_dict)
            with pytest.raises(ModelValidationError):
                element.validate()

    def test_validation_succeeds_on_required_dict(self, deployment_required_dict):
        element = examinee.WebhookDispatcherDeploymentConfig(
            name='foo', raw_dict=deployment_required_dict
        )
        element.validate()

    def test_validation_succeeds_on_unknown_key(self, deployment_required_dict):
        test_dict = {**deployment_required_dict, **{'foo': 'bar'}}
        element = examinee.WebhookDispatcherDeploymentConfig(name='foo', raw_dict=test_dict)
        element.validate()
