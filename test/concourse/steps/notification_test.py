import os
import stat
import tempfile
import unittest

from unittest.mock import MagicMock

import test_utils

from concourse.client.model import BuildStatus
from concourse.steps import step_def
from concourse.model.base import ScriptType
from concourse.model.job import JobVariant
from concourse.model.resources import (
    RepositoryConfig,
    ResourceRegistry,
    ResourceIdentifier,
    Resource,
)
from concourse.model.step import PipelineStep
from concourse.steps import notification
from concourse.model.traits.notifications import (
    NotificationCfgSet,
    NotificationTriggeringPolicy,
)


class NotificationStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.meta_dir = os.path.join(self.tmp_dir.name, 'meta')
        os.mkdir(self.meta_dir)
        test_utils.populate_meta_dir(self.meta_dir)
        self.on_error_dir = os.path.join(self.tmp_dir.name, 'on_error_dir')
        os.mkdir(self.on_error_dir)

        self.job_step = PipelineStep(
            name='step1',
            is_synthetic=False,
            script_type=ScriptType.BOURNE_SHELL,
            raw_dict={},
        )
        self.job_step._notifications_cfg = NotificationCfgSet('default', {}, type_name='cfg_set')
        resource_registry = ResourceRegistry()
        meta_resource_identifier = ResourceIdentifier(
            type_name='meta',
            base_name='a_job',
            branch_name='a_branch',
        )
        meta_resource = Resource(resource_identifier=meta_resource_identifier, raw_dict={})
        resource_registry.add_resource(meta_resource)
        self.job_variant = JobVariant(
            name='a_job',
            raw_dict={},
            resource_registry=resource_registry
        )

        # Set a main repository manually
        test_repo_logical_name = 'test-repository'
        self.job_variant._repos_dict = {}
        self.job_variant._repos_dict[test_repo_logical_name] = RepositoryConfig(
            raw_dict={
                'branch': 'master',
                'hostname': 'github.foo.bar',
                'path': 'test/repo'
            },
            logical_name=test_repo_logical_name,
            qualifier=None,
            is_main_repo=True
        )
        self.job_variant._main_repository_name = test_repo_logical_name

        self.job_variant._traits_dict = {}
        self.cfg_set = MagicMock()
        self.github_cfg = MagicMock()
        self.github_cfg.name = MagicMock(return_value='github_cfg')
        self.email_cfg = MagicMock()
        self.email_cfg.name = MagicMock(return_value='email_cfg')
        self.cfg_set.github = MagicMock(return_value=self.github_cfg)
        self.cfg_set.email = MagicMock(return_value=self.email_cfg)
        ctx_repo_mock = MagicMock(return_value='repo_url')
        ctx_repo_mock.base_url = MagicMock(return_value='repo_url')
        self.cfg_set.ctx_repository = MagicMock(return_value=ctx_repo_mock)

        self.render_step = step_def('notification')

        self.old_cwd = os.getcwd()
        os.chdir(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()
        os.chdir(self.old_cwd)

    def test_render_and_compile(self):
        # as a smoke-test, just try to render
        step_snippet = self.render_step(
            job_step=self.job_step,
            job_variant=self.job_variant,
            cfg_set=self.cfg_set,
            repo_cfgs=(),
            subject='mail_subject1',
            indent=0
        )

        # try to compile (-> basic syntax check)
        return compile(step_snippet, 'notification', 'exec')


class NotificationStepLibTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()
        os.chdir(self.old_cwd)

    def test_job_url(self):
        v = {
            'atc-external-url': 'f://x',
            'build-team-name': 'team',
            'build-pipeline-name': 'pl',
            'build-job-name': 'bjn',
            'build-name': 'bn'
        }
        examinee = notification.job_url
        result = examinee(v)

        self.assertEqual(result, 'f://x/teams/team/pipelines/pl/jobs/bjn/builds/bn')

    def test_should_notify(self):
        examinee = notification.should_notify

        # mock away `determine_previous_build_status` (previous build "succeeded")
        build_status_mock = MagicMock(return_value=BuildStatus.SUCCEEDED)

        # test policies in case previous build succeeded
        assert examinee(
                NotificationTriggeringPolicy.ONLY_FIRST,
                meta_vars={},
                determine_previous_build_status=build_status_mock,
                cfg_set=None,
        )
        assert examinee(
                NotificationTriggeringPolicy.ALWAYS,
                meta_vars={},
                determine_previous_build_status=build_status_mock,
                cfg_set=None,
        )
        assert not examinee(
                NotificationTriggeringPolicy.NEVER,
                meta_vars={},
                determine_previous_build_status=build_status_mock,
                cfg_set=None,
        )

        # test policies in case previous build failed
        build_status_mock = MagicMock(return_value=BuildStatus.FAILED)
        assert not examinee(
                NotificationTriggeringPolicy.ONLY_FIRST,
                meta_vars={},
                determine_previous_build_status=build_status_mock,
                cfg_set=None,
        )
        assert examinee(
                NotificationTriggeringPolicy.ALWAYS,
                meta_vars={},
                determine_previous_build_status=build_status_mock,
                cfg_set=None,
        )
        assert not examinee(
                NotificationTriggeringPolicy.NEVER,
                meta_vars={},
                determine_previous_build_status=build_status_mock,
                cfg_set=None,
        )

    def test_cfg_from_callback(self):
        examinee = notification.cfg_from_callback

        callback_file = os.path.join(self.tmp_dir.name, 'call_me')
        with open(callback_file, 'w') as f:
            f.write('#!/usr/bin/env sh\necho "foo: 42">"${NOTIFY_CFG_OUT}"')
        os.chmod(callback_file, stat.S_IEXEC | stat.S_IREAD)

        assert examinee(
            repo_root=self.tmp_dir.name,
            callback_path=callback_file,
            effective_cfg_file='no-file-yet',
        ) == {'foo':42}
