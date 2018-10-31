import os
import pathlib
import sys
import tempfile
import unittest

from unittest.mock import MagicMock

import test_utils

from concourse.steps import step_def
from concourse.model.step import PipelineStep


class NotificationStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.meta_dir = os.path.join(self.tmp_dir.name, 'meta')
        os.mkdir(self.meta_dir)
        test_utils.populate_meta_dir(self.meta_dir)
        self.on_error_dir = os.path.join(self.tmp_dir.name, 'on_error_dir')
        os.mkdir(self.on_error_dir)

        self.job_step = PipelineStep('step1', raw_dict={})
        self.cfg_set = MagicMock()
        self.github_cfg = MagicMock()
        self.github_cfg.name = MagicMock(return_value='github_cfg')
        self.email_cfg = MagicMock()
        self.email_cfg.name = MagicMock(return_value='email_cfg')
        self.cfg_set.github = MagicMock(return_value=self.github_cfg)
        self.cfg_set.email = MagicMock(return_value=self.email_cfg)

        self.render_step = step_def('notification')

        self.old_cwd = os.getcwd()

    def tearDown(self):
        self.tmp_dir.cleanup()
        os.chdir(self.old_cwd)

    def test_render_and_compile(self):
        # as a smoke-test, just try to render
        step_snippet = self.render_step(
            job_step=self.job_step,
            cfg_set=self.cfg_set,
            repo_cfgs=(),
            subject='mail_subject1',
            indent=0
        )

        # try to compile (-> basic syntax check)
        return compile(step_snippet, 'notification', 'exec')
