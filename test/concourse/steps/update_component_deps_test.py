import tempfile
import unittest
import os
import pathlib

import test_utils

from concourse.steps import step_def
import concourse.model.traits.component_descriptor as component_descriptor


class ComponentDescriptorStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()

        self.render_step = step_def('update_component_deps')

        self.main_repo = test_utils.repository()

        repo_dir = pathlib.Path(self.tmp_dir.name, self.main_repo.resource_name())
        repo_dir.mkdir()

        self.job_variant = test_utils.job(self.main_repo)

        self.old_cwd = os.getcwd()

    def tearDown(self):
        self.tmp_dir.cleanup()
        os.chdir(self.old_cwd)

    def test_render_and_compile(self):
        # as a smoke-test, just try to render
        step_snippet = self.render_step(
            job_step=None,
            job_variant=self.job_variant,
            github_cfg_name=None,
            indent=0
        )

        # try to compile (-> basic syntax check)
        return compile(step_snippet, 'component_descriptor.mako', 'exec')
