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


def test_determine_reference_versions():

    # Case 1: No Upstream
    greatest_version = '2.1.1'
    examinee = functools.partial(
        determine_reference_versions,
        component_name='example.org/foo/bar',
    )
    with unittest.mock.patch('product.v2') as product_mock:
        with unittest.mock.patch(
            'concourse.steps.update_component_deps.current_base_url'
        ) as base_url_mock:
            product_mock.latest_component_version.return_value = greatest_version
            base_url_mock.return_value = "foo"

            # no upstream component -> expect latest version to be returned
            assert examinee(
                    reference_version='2.1.0',
                    upstream_component_name=None,
                ) == (greatest_version,)

            product_mock.latest_component_version.assert_called()
            base_url_mock.assert_called()

            product_mock.latest_component_version.reset_mock()
            base_url_mock.reset_mock()

            assert examinee(
                    reference_version='2.2.0', # same result, if our version is already greater
                    upstream_component_name=None,
                ) == (greatest_version,)

            product_mock.latest_component_version.assert_called()
            base_url_mock.assert_called()

    # Case 2: Upstream component defined
    examinee = functools.partial(
        determine_reference_versions,
        component_name='example.org/foo/bar',
        upstream_component_name='example.org/foo/bar',
    )

    with unittest.mock.patch('product.v2') as product_mock:
        with unittest.mock.patch(
            'concourse.steps.update_component_deps.current_base_url'
        ) as base_url_mock:

            upstream_version = '2.2.0'
            UUP = update_component_deps.UpstreamUpdatePolicy

            product_mock.latest_component_version.return_value = upstream_version
            base_url_mock.return_value = "foo"

            # should return upstream version, by default (default to strict-following)
            assert examinee(
                reference_version='1.2.3', # does not matter
            ) == (upstream_version,)

            product_mock.latest_component_version.assert_called_once()
            base_url_mock.assert_called_once()

            product_mock.latest_component_version.reset_mock()
            base_url_mock.reset_mock()

            # same behaviour if explicitly configured
            assert examinee(
                reference_version='1.2.3', # does not matter
                upstream_update_policy=UUP.STRICTLY_FOLLOW,
            ) == (upstream_version,)

            product_mock.latest_component_version.assert_called_once()
            base_url_mock.assert_called_once()

            product_mock.latest_component_version.reset_mock()
            base_url_mock.reset_mock()

            # if not strictly following, should consider hotfix
            upstream_hotfix_version = '2.2.3'
            product_mock.greatest_component_version_with_matching_minor.return_value = \
                upstream_hotfix_version

            assert examinee(
                reference_version='1.2.3', # does not matter
                upstream_update_policy=UUP.ACCEPT_HOTFIXES,
            ) == (upstream_hotfix_version, upstream_version)

            product_mock.latest_component_version.assert_called_once()
            base_url_mock.assert_called_once()
            product_mock.greatest_component_version_with_matching_minor.assert_called_once()
