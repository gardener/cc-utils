# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import unittest
from unittest.mock import MagicMock

import concourse.model.traits.component_descriptor as trait_module
from concourse.model.job import JobVariant


def component_descriptor_transformer(trait_dict: dict):
    trait = trait_module.ComponentDescriptorTrait(
        variant_name='does_not_matter',
        name='does_not_matter',
        raw_dict=trait_dict,
    )
    examinee = trait.transformer()
    return examinee


class ComponentDescriptorTraitTransformerTest(unittest.TestCase):
    def setUp(self):
        self.pipeline_args = JobVariant(
            name='a_job',
            raw_dict={},
            resource_registry=object(),
        )
        self.pipeline_args._steps_dict = {}
        self.pipeline_args._traits_dict = {}
        self.repo_mock = MagicMock()
        self.repo_mock.repo_hostname = MagicMock(return_value='github.com')
        self.repo_mock.repo_path = MagicMock(return_value='org/repo')
        self.pipeline_args.main_repository = MagicMock(return_value=self.repo_mock)

    def test_process_pipeline_args_injects_component_name(self):
        examinee = component_descriptor_transformer(trait_dict={})

        examinee.process_pipeline_args(pipeline_args=self.pipeline_args)

        self.assertEqual(
            examinee.trait.component_name(),
            'github.com/org/repo',
        )

    def test_process_pipeline_args_leaves_configured_component_name(self):
        examinee = component_descriptor_transformer(
            trait_dict={'component_name': 'foo.org/p/r'}
        )

        examinee.process_pipeline_args(pipeline_args=self.pipeline_args)

        self.assertEqual(
            examinee.trait.component_name(),
            'foo.org/p/r',
        )
