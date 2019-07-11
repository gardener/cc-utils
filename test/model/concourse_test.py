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

import model.concourse as examinee
from model.base import ModelValidationError


class ConcourseTeamTest(object):
    @pytest.fixture
    def concourse_team_dict(self):
        return {
            'username': 'foo',
            'password': 'baz',
            'git_auth_team': 'foo:bar',
            'github_auth_client_id': 'foobarbaz',
            'github_auth_client_secret': 'hush',
        }

    def test_team_credentials_complete_basic_auth_detected(self, concourse_team_dict):
        test_object = examinee.ConcourseTeam(name='foo', raw_dict=concourse_team_dict)
        self.assertTrue(test_object.has_basic_auth_credentials())

    def test_team_credentials_complete_github_oauth_detected(self, concourse_team_dict):
        test_object = examinee.ConcourseTeam(name='foo', raw_dict=concourse_team_dict)
        self.assertTrue(test_object.has_github_oauth_credentials())

    def test_git_auth_team_getter(self, concourse_team_dict):
        test_object = examinee.ConcourseTeam(name='foo', raw_dict=concourse_team_dict)

        org, team = test_object.github_auth_team(split=True)
        self.assertEqual(org, 'foo')
        self.assertEqual(team, 'bar')

        org_team = test_object.github_auth_team(split=False)
        self.assertEqual(org_team, 'foo:bar')

    def test_validation_fails_on_missing_basic_auth_value(self, concourse_team_dict):
        for key in ('username', 'password'):
            with self.subTest(value=key):
                test_dict = concourse_team_dict.copy()
                test_dict.pop(key)
                element = examinee.ConcourseTeam(name='foo', raw_dict=test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()

    def test_validation_fails_on_missing_github_oauth_value(self, concourse_team_dict):
        for key in ('git_auth_team', 'github_auth_client_id', 'github_auth_client_secret'):
            with self.subTest(value=key):
                test_dict = concourse_team_dict.copy()
                test_dict.pop(key)
                element = examinee.ConcourseTeam(name='foo', raw_dict=test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()

    def test_validation_fails_on_invalid_github_oauth_teamname(self, concourse_team_dict):
        for value in ('foo/bar/baz', '/foo', 'bar/', 'baz'):
            with self.subTest(value=value):
                test_dict = concourse_team_dict.copy()
                test_dict['git_auth_team'] = value
                element = examinee.ConcourseTeam(name='foo', raw_dict=test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()


class ConcourseConfigTest(object):
    @pytest.fixture
    def required_dict(self):
        return {
            'externalUrl': 'foo',
            'concourse_uam_config': 'foo',
            'helm_chart_default_values_config': 'foo',
            'kubernetes_cluster_config': 'foo',
            'concourse_version': examinee.ConcourseApiVersion.V5,
            'job_mapping': 'foo',
            'imagePullSecret': 'foo',
            'tls_secret_name': 'foo',
            'tls_config': 'foo',
            'ingress_host': 'foo',
            'helm_chart_version': 'foo',
            'helm_chart_values': 'foo',
        }

    def test_validation_fails_on_missing_required_key(self, required_dict):
        for key in required_dict.keys():
            test_dict = required_dict.copy()
            test_dict.pop(key)
            element = examinee.ConcourseConfig(name='foo', raw_dict=test_dict)
            with pytest.raises(ModelValidationError):
                element.validate()

    def test_validation_succeeds_on_required_dict(self, required_dict):
        element = examinee.ConcourseConfig(name='foo', raw_dict=required_dict)
        element.validate()

    def test_validation_fails_on_unknown_key(self, required_dict):
        # since optional attributes are defined for ConcourseConfig, test should fail
        test_dict = {**required_dict, **{'foo': 'bar'}}
        element = examinee.ConcourseConfig(name='foo', raw_dict=test_dict)
        with pytest.raises(ModelValidationError):
            element.validate()
