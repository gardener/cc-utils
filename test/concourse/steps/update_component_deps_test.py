import tempfile
import unittest
import os
import pathlib

import gci.componentmodel as cm

import test_utils

from concourse.steps import step_def
import concourse.model.traits.update_component_deps as update_component_deps
import concourse.model.traits.component_descriptor as component_descriptor


class UpdateComponentDependenciesStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()

        self.render_step = step_def('update_component_deps')

        self.update_component_deps_trait = update_component_deps.UpdateComponentDependenciesTrait(
            name='update_component_dependencies',
            variant_name='don\'t_care',
            raw_dict={
                'set_dependency_version_script':'some_path',
                'upstream_component_name':'don\'t_care',
            },
        )

        self.component_descriptor_trait = component_descriptor.ComponentDescriptorTrait(
            name='component_descriptor',
            variant_name='don\'t_care',
            raw_dict={
                'component_name': 'github.com/org/repo_name',
            },
        )
        self.component_descriptor_trait.ctx_repository = lambda: cm.OciOcmRepository(
            baseUrl='dummy-base-url',
        )

        self.main_repo = test_utils.repository()

        repo_dir = pathlib.Path(self.tmp_dir.name, self.main_repo.resource_name())
        repo_dir.mkdir()

        self.job_variant = test_utils.job(self.main_repo)
        self.job_variant._traits_dict = {
            'update_component_deps': self.update_component_deps_trait,
            'component_descriptor': self.component_descriptor_trait,
        }

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
        return compile(step_snippet, 'update_component_deps.mako', 'exec')
