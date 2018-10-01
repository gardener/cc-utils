import unittest

import test_utils

from concourse.steps import step_def
import concourse.model.traits.version as version_trait

class VersionStepText(unittest.TestCase):
    def setUp(self):
        self.version_trait = version_trait.VersionTrait(
            name='version',
            variant_name='don\'t_care',
            raw_dict={},
        )
        self.version_trait_transformer = self.version_trait.transformer()
        self.render_step = step_def('version')

        self.main_repo = test_utils.repository()
        self.job_variant = test_utils.job(self.main_repo)

        self.job_variant._traits_dict = {'version': self.version_trait}

        for step in self.version_trait_transformer.inject_steps():
            self.job_variant._steps_dict[step.name] = step

    def test_render(self):
        # as a smoke-test, just try to render
        self.render_step(
            job_step=self.job_variant.step('version'),
            job_variant=self.job_variant,
            indent=0
        )
