import tempfile
import functools
import unittest
import os
import pathlib

import test_utils

from concourse.steps import step_def
from concourse.steps.update_component_deps import (
    determine_reference_versions,
)
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


def test_determine_reference_versions():
    # Case 1: No Upstream
    greatest_version = '2.1.1'
    component_name = 'example.org/foo/bar'
    base_url = "foo" # actual value not relevant here
    examinee = functools.partial(
        determine_reference_versions,
        component_name=component_name,
    )
    with unittest.mock.patch('product.v2') as product_mock:
        product_mock.latest_component_version.return_value = greatest_version

        # no upstream component -> expect latest version to be returned
        assert examinee(
                reference_version='2.1.0',
                upstream_component_name=None,
                repository_ctx_base_url=base_url,
            ) == (greatest_version,)

        product_mock.latest_component_version.assert_called_with(
            component_name=component_name,
            ctx_repo_base_url=base_url,
        )

        product_mock.latest_component_version.reset_mock()

        assert examinee(
                reference_version='2.2.0', # same result, if our version is already greater
                upstream_component_name=None,
                repository_ctx_base_url=base_url,
            ) == (greatest_version,)

        product_mock.latest_component_version.assert_called_with(
            component_name=component_name,
            ctx_repo_base_url=base_url,
        )

    # Case 2: Upstream component defined
    examinee = functools.partial(
        determine_reference_versions,
        component_name='example.org/foo/bar',
        upstream_component_name='example.org/foo/bar',
    )

    with unittest.mock.patch(
        'concourse.steps.update_component_deps.latest_component_version_from_upstream'
    ) as upstream_version_mock:

        upstream_version = '2.2.0'
        UUP = update_component_deps.UpstreamUpdatePolicy

        upstream_version_mock.return_value = upstream_version

        # should return upstream version, by default (default to strict-following)
        assert examinee(
            reference_version='1.2.3', # does not matter
            repository_ctx_base_url=base_url,
        ) == (upstream_version,)

        upstream_version_mock.assert_called_once_with(
            component_name=component_name,
            upstream_component_name='example.org/foo/bar',
            base_url=base_url,
        )

        upstream_version_mock.reset_mock()

        # same behaviour if explicitly configured
        assert examinee(
            reference_version='1.2.3', # does not matter
            upstream_update_policy=UUP.STRICTLY_FOLLOW,
            repository_ctx_base_url=base_url,
        ) == (upstream_version,)

        upstream_version_mock.assert_called_once_with(
            component_name=component_name,
            upstream_component_name='example.org/foo/bar',
            base_url=base_url,
        )

        upstream_version_mock.reset_mock()

        with unittest.mock.patch('product.v2') as product_mock:
            # if not strictly following, should consider hotfix
            reference_version = '1.2.3'
            upstream_hotfix_version = '2.2.3'
            product_mock.greatest_component_version_with_matching_minor.return_value = \
                upstream_hotfix_version

            assert examinee(
                reference_version=reference_version, # does not matter
                upstream_update_policy=UUP.ACCEPT_HOTFIXES,
                repository_ctx_base_url=base_url,
            ) == (upstream_hotfix_version, upstream_version)

            upstream_version_mock.assert_called_once_with(
                component_name=component_name,
                upstream_component_name='example.org/foo/bar',
                base_url=base_url,
            )
            product_mock.greatest_component_version_with_matching_minor.assert_called_once_with(
                component_name=component_name,
                ctx_repo_base_url=base_url,
                reference_version=reference_version,
            )
