# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import unittest

import model as examinee
from model.base import ModelValidationError

class TeamCredentialTest(unittest.TestCase):
    def setUp(self):
        self.raw_dict = TeamCredentialTest.create_valid_test_dictionary()

    @staticmethod
    def create_valid_test_dictionary():
        return {
            'teamname': 'bar',
            'username': 'foo',
            'password': 'baz',
            'gitAuthTeam': 'foo/bar',
            'githubAuthClientId': 'foobarbaz',
            'githubAuthClientSecret': 'hush',
            'githubAuthAuthUrl': 'foo://some.url',
            'githubAuthApiUrl': 'bar://another.url',
            'githubAuthTokenUrl': 'baz://yet.another.url',
        }

    def test_team_credentials_complete_basic_auth_detected(self):
        test_object = examinee.ConcourseTeamCredentials(self.raw_dict)
        self.assertTrue(test_object.has_basic_auth_credentials())

    def test_team_credentials_complete_github_oauth_detected(self):
        test_object = examinee.ConcourseTeamCredentials(self.raw_dict)
        self.assertTrue(test_object.has_github_oauth_credentials())

    def test_validation_fails_on_empty_dict(self):
        raw_dict = {}
        with self.assertRaises(ModelValidationError):
            examinee.ConcourseTeamCredentials(raw_dict)

    def test_git_auth_team_getter(self):
        test_object = examinee.ConcourseTeamCredentials(self.raw_dict)

        org, team = test_object.github_auth_team(split=True)
        self.assertEqual(org, 'foo')
        self.assertEqual(team, 'bar')

        org_team = test_object.github_auth_team(split=False)
        self.assertEqual(org_team, 'foo/bar')

    def test_validation_fails_on_missing_teamname(self):
        self.raw_dict.pop('teamname')
        with self.assertRaises(ModelValidationError):
            examinee.ConcourseTeamCredentials(self.raw_dict)

    def test_validation_fails_on_missing_basic_auth_value(self):
        for key in ('username', 'password'):
            with self.subTest(value=key):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict.pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.ConcourseTeamCredentials(test_dict)

    def test_validation_fails_on_missing_github_oauth_value(self):
        for key in ('gitAuthTeam', 'githubAuthClientId', 'githubAuthClientSecret'):
            with self.subTest(value=key):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict.pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.ConcourseTeamCredentials(test_dict)

    def test_validation_fails_on_missing_github_oauth_urls(self):
        for key in ('githubAuthAuthUrl', 'githubAuthApiUrl', 'githubAuthTokenUrl'):
            with self.subTest(url=key):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict.pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.ConcourseTeamCredentials(test_dict)

    def test_validation_fails_on_invalid_github_oauth_teamname(self):
        for value in ('foo/bar/baz', '/foo', 'bar/', 'baz'):
            with self.subTest(value=value):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict['gitAuthTeam'] = value
                with self.assertRaises(ModelValidationError):
                    examinee.ConcourseTeamCredentials(test_dict)


class EmailConfigTest(unittest.TestCase):
    def setUp(self):
        self.raw_dict = EmailConfigTest.create_valid_test_dictionary()

    @staticmethod
    def create_valid_test_dictionary():
        return {
            'host': 'mail.foo.bar',
            'port': 666,
            'technicalUser': {'username': 'u', 'password': 'p'}
        }

    def test_validation_fails_on_missing_key(self):
        for key in ('host', 'port', 'technicalUser'):
            with self.subTest(key=key):
                test_dict = EmailConfigTest.create_valid_test_dictionary()
                test_dict.pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.EmailConfig(name='foo', raw_dict=test_dict)

    def test_validation_fails_on_invalid_credentials(self):
        for key in ('username', 'password'):
            with self.subTest(key=key):
                test_dict = EmailConfigTest.create_valid_test_dictionary()
                test_dict['technicalUser'].pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.EmailConfig(name='bar', raw_dict=test_dict)


class GithubConfigTest(unittest.TestCase):
    def setUp(self):
        self.raw_dict = GithubConfigTest.create_valid_test_dictionary()

    @staticmethod
    def create_valid_test_dictionary():
        return {
            'sshUrl': 'ssh://foo@bar.baz',
            'httpUrl': 'https://foo.bar',
            'apiUrl': 'https://api.foo.bar',
            'disable_tls_validation': True,
            'webhook_token': 'foobarbaz',
            'technicalUser': {
                'username': 'foo',
                'password': 'bar',
                'authToken': 'foobar',
                'privateKey': 'barfoobaz',
            },
        }

    def test_validation_fails_on_missing_key(self):
        for key in ('sshUrl', 'httpUrl', 'apiUrl', 'disable_tls_validation', 'webhook_token', 'technicalUser'):
            with self.subTest(key=key):
                test_dict = GithubConfigTest.create_valid_test_dictionary()
                test_dict.pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.GithubConfig(name='gitabc', raw_dict=test_dict)

    def test_validation_fails_on_invalid_technicalUser(self):
        for key in ('username', 'password', 'authToken', 'privateKey'):
            with self.subTest(key=key):
                test_dict = GithubConfigTest.create_valid_test_dictionary()
                test_dict['technicalUser'].pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.GithubConfig(name='anothergit', raw_dict=test_dict)


class ConcourseConfigTest(unittest.TestCase):
    def setUp(self):
        self.raw_dict = ConcourseConfigTest.create_valid_test_dictionary()

    @staticmethod
    def create_valid_test_dictionary():
        return {
            'externalUrl': 'foo://bar.baz',
            'proxyUrl': 'bar://foo.baz',
            'teams': {
                'main': TeamCredentialTest.create_valid_test_dictionary(),
            },
            'helm_chart_default_values_config':'foo',
            'deploy_delaying_proxy':True,
            'kubernetes_cluster_config': 'bar'
        }

    def test_validation_fails_on_missing_key(self):
        for key in ('externalUrl', 'teams', 'helm_chart_default_values_config','kubernetes_cluster_config'):
            with self.subTest(key=key):
                test_dict = ConcourseConfigTest.create_valid_test_dictionary()
                test_dict.pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.ConcourseConfig(name='x', raw_dict=test_dict)

    def test_validation_fails_on_empty_teams(self):
        self.raw_dict['teams'].pop('main')
        with self.assertRaises(ModelValidationError):
            examinee.ConcourseConfig(name='ateam', raw_dict=self.raw_dict)

    def test_validation_fails_on_absent_main_team(self):
        self.raw_dict['teams'].pop('main')
        self.raw_dict['teams']['foo'] = TeamCredentialTest.create_valid_test_dictionary()
        with self.assertRaises(ModelValidationError):
            examinee.ConcourseConfig(name='bteam', raw_dict=self.raw_dict)

    def test_validation_fails_on_invalid_team(self):
        self.raw_dict['teams']['main'].pop('teamname')
        with self.assertRaises(ModelValidationError):
            examinee.ConcourseConfig(name='cteam', raw_dict=self.raw_dict)

    def test_validation_fails_on_absent_proxy_url_when_proxy_is_configured(self):
        self.raw_dict.pop('proxyUrl')
        with self.assertRaises(ModelValidationError):
            examinee.ConcourseConfig(name='dteam', raw_dict=self.raw_dict)


class BasicCredentialsTest(unittest.TestCase):
    def setUp(self):
        self.raw_dict = {
            'username':'foo',
            'password':'bar',
        }

    def test_validation_fails_on_missing_key(self):
        for key in ('username', 'password'):
            with self.subTest(key=key):
                test_dict = self.raw_dict.copy()
                test_dict.pop(key)
                with self.assertRaises(ModelValidationError):
                    examinee.BasicCredentials(test_dict)

