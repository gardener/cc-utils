# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

from urllib.parse import parse_qs, urlparse

from concourse import client
from github.webhook import WebhookQueryAttributes


class ConcourseApiRoutesTest(unittest.TestCase):
    def setUp(self):
        self.examinee = client.ConcourseApiRoutesBase(
            base_url='https://made-up-concourse.com',
            team='foo'
        )

    def test_team_route(self):
        self.assertEqual(self.examinee.team_url(), 'https://made-up-concourse.com/api/v1/teams/foo')
        self.assertEqual(
            self.examinee.team_url(team='bar'),
            'https://made-up-concourse.com/api/v1/teams/bar'
        )

    def test_pipelines_route(self):
        self.assertEqual(
            self.examinee.pipelines(),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines'
        )

    def test_order_pipelines_route(self):
        self.assertEqual(
            self.examinee.order_pipelines(),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/ordering',
        )

    def test_pipeline_route(self):
        self.assertEqual(
            self.examinee.pipeline(pipeline_name='baz'),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/baz',
        )

    def test_pipeline_config_route(self):
        self.assertEqual(
            self.examinee.pipeline_cfg(pipeline_name='baz'),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/baz/config',
        )

    def test_unpause_pipeline_route(self):
        self.assertEqual(
            self.examinee.unpause_pipeline(pipeline_name='baz'),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/baz/unpause',
        )

    def test_unpause_expose_route(self):
        self.assertEqual(
            self.examinee.expose_pipeline(pipeline_name='baz'),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/baz/expose',
        )

    def test_resource_check_route(self):
        self.assertEqual(
            self.examinee.resource_check(pipeline_name='baz', resource_name='bar'),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/baz/resources/bar/check',
        )

    def test_resource_check_webhook_route(self):
        webhook_route = self.examinee.resource_check_webhook(
            pipeline_name='baz',
            resource_name='bar',
            query_attributes=WebhookQueryAttributes(
                webhook_token='made-up-token',
                concourse_id='made-up-concourse',
                job_mapping_id='made-up-mapping',
            ),
        )

        scheme, netloc, path, _, query, _ = urlparse(webhook_route)
        self.assertEqual(scheme, 'https')
        self.assertEqual(netloc, 'made-up-concourse.com')
        self.assertEqual(path, '/api/v1/teams/foo/pipelines/baz/resources/bar/check/webhook')
        query_data = parse_qs(query)
        self.assertEqual(
            {
                'webhook_token': ['made-up-token'],
                'concourse_id': ['made-up-concourse'],
                'job_mapping_id': ['made-up-mapping'],
            },
            query_data,
        )

    def test_job_build_route(self):
        self.assertEqual(
            self.examinee.job_build(pipeline_name='baz', job_name='bar', build_name='123'),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/baz/jobs/bar/builds/123',
        )

    def test_job_builds_route(self):
        self.assertEqual(
            self.examinee.job_builds(pipeline_name='baz', job_name='bar'),
            'https://made-up-concourse.com/api/v1/teams/foo/pipelines/baz/jobs/bar/builds',
        )

    def test_build_events_route(self):
        self.assertEqual(
            self.examinee.build_events(build_id=252525),
            'https://made-up-concourse.com/api/v1/builds/252525/events',
        )

    def test_build_plan_route(self):
        self.assertEqual(
            self.examinee.build_plan(build_id=252525),
            'https://made-up-concourse.com/api/v1/builds/252525/plan',
        )


class ConcourseApiRoutesV3Test(unittest.TestCase):
    def setUp(self):
        self.examinee = client.ConcourseApiRoutesV3(
            base_url='https://made-up-concourse.com',
            team='foo'
        )

    def test_login_route(self):
        self.assertEqual(
            self.examinee.login(),
            'https://made-up-concourse.com/auth/basic/token?team_name=foo'
        )


class ConcourseApiRoutesV4Test(unittest.TestCase):
    def setUp(self):
        self.examinee = client.ConcourseApiRoutesV4(
            base_url='https://made-up-concourse.com',
            team='foo'
        )

    def test_login_route(self):
        self.assertEqual(
            self.examinee.login(),
            'https://made-up-concourse.com/sky/token'
        )
