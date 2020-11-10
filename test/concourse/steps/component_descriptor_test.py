import git
import tempfile
import unittest
import os
import pathlib

import test_utils

import gci.componentmodel
from concourse.steps import step_def
import concourse.model.traits.component_descriptor as component_descriptor


# make linter happy
if gci.componentmodel.SchemaVersion.V2 == None:
    pass


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
        git.Repo.init(path=repo_dir)

        self.job_variant = test_utils.job(self.main_repo)

        self.job_variant._traits_dict = {'component_descriptor': self.component_descriptor_trait}

        for step in self.component_descriptor_transformer.inject_steps():
            self.job_variant._steps_dict[step.name] = step

        self.component_descriptor_step = self.job_variant.step(
            component_descriptor.DEFAULT_COMPONENT_DESCRIPTOR_STEP_NAME,
        )
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
        return compile(step_snippet, 'component_descriptor.mako', 'exec')

    def test_smoke(self):
        os.chdir(self.tmp_dir.name)

        # fulfill runtime requirements

        # version file
        version_file_dir = pathlib.Path(self.tmp_dir.name, 'version_path')
        version_file_dir.mkdir()
        version_file = version_file_dir.joinpath('version')
        version_file.write_text('1.2.3')

        # component descriptor output directory
        component_descriptor_dir = pathlib.Path(self.tmp_dir.name, 'component_descriptor_dir')
        component_descriptor_dir.mkdir()

        compiled_step = self.test_render_and_compile()
        try:
            eval(compiled_step)
        except SystemExit:
            pass

        generated_component_descriptor = component_descriptor_dir.joinpath(
            'component_descriptor_v1'
        )
        self.assertTrue(generated_component_descriptor.is_file())
        # todo: parse and validate contents
