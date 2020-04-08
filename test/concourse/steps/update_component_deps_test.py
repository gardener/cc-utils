import tempfile
import functools
import unittest
import os
import pathlib
from unittest.mock import MagicMock

import test_utils

from concourse.steps import step_def
from concourse.steps.update_component_deps import (
    current_product_descriptor,
    determine_reference_version,
)
import concourse.model.traits.update_component_deps as update_component_deps
import product.util


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

        self.main_repo = test_utils.repository()

        repo_dir = pathlib.Path(self.tmp_dir.name, self.main_repo.resource_name())
        repo_dir.mkdir()

        self.job_variant = test_utils.job(self.main_repo)
        self.job_variant._traits_dict = {'update_component_deps': self.update_component_deps_trait}

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


def test_current_product_descriptor(tmpdir):
    os.environ['COMPONENT_DESCRIPTOR_DIR'] = str(tmpdir)
    tmpdir.join('component_descriptor').write('{}')

    assert current_product_descriptor().raw == {'components': [], 'component_overwrites': []}


def test_determine_reference_version():
    greatest_version = '2.1.1'
    component_resolver = product.util.ComponentResolver()
    component_resolver.latest_component_version = MagicMock(return_value=greatest_version)
    component_descriptor_resolver = product.util.ComponentDescriptorResolver()

    examinee = functools.partial(
        determine_reference_version,
        component_name='example.org/foo/bar',
        component_resolver=component_resolver,
        component_descriptor_resolver=component_descriptor_resolver,
    )

    # no upstream component -> expect latest version to be returned
    assert examinee(
            reference_version='2.1.0',
            upstream_component_name=None,
        ) == greatest_version
    assert examinee(
            reference_version='2.2.0', # same result, if our version is already greater
            upstream_component_name=None,
        ) == greatest_version
