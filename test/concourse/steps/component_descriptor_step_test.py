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

        self.component_descriptor_trait = component_descriptor.ComponentDescriptorTrait(
            name='component_descriptor',
            variant_name='don\'t_care',
            raw_dict={
                'component_name': 'github.com/org/repo_name',
            },
        )
        self.component_descriptor_transformer = self.component_descriptor_trait.transformer()
        self.render_step = step_def('component_descriptor')

        self.main_repo = test_utils.repository()

        repo_dir = pathlib.Path(self.tmp_dir.name, self.main_repo.resource_name())
        repo_dir.mkdir()

        self.job_variant = test_utils.job(self.main_repo)

        self.job_variant._traits_dict = {'component_descriptor': self.component_descriptor_trait}

        for step in self.component_descriptor_transformer.inject_steps():
            self.job_variant._steps_dict[step.name] = step

        self.component_descriptor_step = self.job_variant.step('component_descriptor')
        self.component_descriptor_step.add_input('version_path', 'version_path')

        self.old_cwd = os.getcwd()

    def tearDown(self):
        self.tmp_dir.cleanup()
        os.chdir(self.old_cwd)

    def test_render_and_compile(self):
        # as a smoke-test, just try to render
        step_snippet = self.render_step(
            job_step=self.job_variant.step('component_descriptor'),
            job_variant=self.job_variant,
            output_image_descriptors={},
            indent=0
        )

        # try to compile (-> basic syntax check)
        return compile(step_snippet, 'version', 'exec')

    def test_smoke(self):
        # TODO: implement smoke-test executing the code (this will pbly require some
        # additional work to mock aways access to github api)
        return
