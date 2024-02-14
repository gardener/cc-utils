# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import unittest

from concourse.client import routes


class ConcourseApiRoutesBaseTest(unittest.TestCase):
    def setUp(self):
        self.examinee = routes.ConcourseApiRoutesBase(
            base_url='https://made-up-concourse.com',
            team='foo'
        )

    def test_team_route(self):
        self.assertEqual(self.examinee.team_url(), 'https://made-up-concourse.com/api/v1/teams/foo')
        self.assertEqual(
            self.examinee.team_url(team='bar'),
            'https://made-up-concourse.com/api/v1/teams/bar'
        )

    def test_login_route(self):
        self.assertEqual(
            self.examinee.login(),
            'https://made-up-concourse.com/sky/token'
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

    def test_list_worker_route(self):
        self.assertEqual(
            self.examinee.list_workers(),
            'https://made-up-concourse.com/api/v1/workers',
        )

    def test_prune_worker_route(self):
        self.assertEqual(
            self.examinee.prune_worker(worker_name='foo'),
            'https://made-up-concourse.com/api/v1/workers/foo/prune',
        )
