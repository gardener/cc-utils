import tempfile
import unittest
import os
import pathlib

import test_utils

from concourse.steps import step_def
import concourse.model.traits.version as version_trait


class VersionStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.version_outdir = pathlib.Path(self.tmp_dir.name, 'managed-version')
        self.version_outdir.mkdir()

        self.version_trait = version_trait.VersionTrait(
            name='version',
            variant_name='don\'t_care',
            raw_dict={
                'versionfile': 'version',
                'preprocess': 'finalize',
            },
        )
        self.version_trait_transformer = self.version_trait.transformer()
        self.render_step = step_def('version')

        self.main_repo = test_utils.repository()

        repo_dir = pathlib.Path(self.tmp_dir.name, self.main_repo.resource_name())
        repo_dir.mkdir()

        # create fake git dir
        os.mkdir(repo_dir.joinpath('.git'))
        repo_dir.joinpath('.git', 'HEAD').touch()

        self.version_file = repo_dir.joinpath('version')
        self.version_file.write_text('1.2.3-xxx')

        self.job_variant = test_utils.job(self.main_repo)

        self.job_variant._traits_dict = {'version': self.version_trait}

        for step in self.version_trait_transformer.inject_steps():
            self.job_variant._steps_dict[step.name] = step

        self.old_cwd = os.getcwd()

    def tearDown(self):
        self.tmp_dir.cleanup()
        os.chdir(self.old_cwd)

    def test_render_and_compile(self):
        # as a smoke-test, just try to render
        step_snippet = self.render_step(
            job_step=self.job_variant.step('version'),
            job_variant=self.job_variant,
            indent=0
        )

        # try to compile (-> basic syntax check)
        return compile(step_snippet, 'version', 'exec')

    def test_smoke(self):
        compiled = self.test_render_and_compile()

        os.chdir(self.tmp_dir.name)

        eval(compiled)

        # check that version preprocessing was actually done
        effective_version = pathlib.Path(self.tmp_dir.name, 'managed-version', 'version')
        self.assertEqual(effective_version.read_text(), '1.2.3')
