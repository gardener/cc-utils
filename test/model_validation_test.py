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

import unittest

import model.concourse as examinee
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
            'gitAuthTeam': 'foo:bar',
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
            examinee.ConcourseTeamCredentials(raw_dict).validate()

    def test_git_auth_team_getter(self):
        test_object = examinee.ConcourseTeamCredentials(self.raw_dict)

        org, team = test_object.github_auth_team(split=True)
        self.assertEqual(org, 'foo')
        self.assertEqual(team, 'bar')

        org_team = test_object.github_auth_team(split=False)
        self.assertEqual(org_team, 'foo:bar')

    def test_validation_fails_on_missing_teamname(self):
        self.raw_dict.pop('teamname')
        element = examinee.ConcourseTeamCredentials(self.raw_dict)
        with self.assertRaises(ModelValidationError):
            element.validate()

    def test_validation_fails_on_missing_basic_auth_value(self):
        for key in ('username', 'password'):
            with self.subTest(value=key):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict.pop(key)
                element = examinee.ConcourseTeamCredentials(test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()

    def test_validation_fails_on_missing_github_oauth_value(self):
        for key in ('gitAuthTeam', 'githubAuthClientId', 'githubAuthClientSecret'):
            with self.subTest(value=key):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict.pop(key)
                element = examinee.ConcourseTeamCredentials(test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()

    def test_validation_fails_on_missing_github_oauth_urls(self):
        for key in ('githubAuthAuthUrl', 'githubAuthApiUrl', 'githubAuthTokenUrl'):
            with self.subTest(url=key):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict.pop(key)
                element = examinee.ConcourseTeamCredentials(test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()

    def test_validation_fails_on_invalid_github_oauth_teamname(self):
        for value in ('foo/bar/baz', '/foo', 'bar/', 'baz'):
            with self.subTest(value=value):
                test_dict = TeamCredentialTest.create_valid_test_dictionary()
                test_dict['gitAuthTeam'] = value
                element = examinee.ConcourseTeamCredentials(test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()


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
                element = examinee.BasicCredentials(test_dict)
                with self.assertRaises(ModelValidationError):
                    element.validate()
