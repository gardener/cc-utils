import unittest

from model.base import ModelValidationError
from concourse.factory import (
    RawPipelineDefinitionDescriptor as DefDescriptor,
    DefinitionFactory
)


class RawPipelineDefinitionDescriptorTest(unittest.TestCase):
    def test_basic_validation(self):
        # "OK case"
        DefDescriptor(name='a_name', base_definition=None, variants={'foo': 42})

        # error cases
        with self.assertRaises(ValueError):
            DefDescriptor(name=None, base_definition=None, variants={'foo': 42})
        with self.assertRaises(ModelValidationError):
            DefDescriptor(name='a_name', base_definition=None, variants={})


class DefinitionFactoryTest(unittest.TestCase):
    def test_valid_validation(self):
        # "OK case"
        descriptor = DefDescriptor(name='a_name', base_definition=None, variants={'foo': 42})
        DefinitionFactory(raw_definition_descriptor=descriptor)

        with self.assertRaises(ValueError):
            DefinitionFactory(raw_definition_descriptor=None)

    def test_inheritance(self):
        base_def = {'foo': 'bar', 123: 555}
        variants = {
            'variant_a': {'foo': 42},
            'variant_b': {'xxx': 31}
        }
        descriptor = DefDescriptor(name='x_name', base_definition=base_def, variants=variants)
        factory = DefinitionFactory(raw_definition_descriptor=descriptor)

        merged_variants = factory._create_variants_dict(descriptor)

        self.assertEqual(set(merged_variants.keys()), {'variant_a', 'variant_b'})

        variant_a = merged_variants['variant_a']
        variant_b = merged_variants['variant_b']

        # variants may overwrite values
        self.assertEqual(variant_a, {'foo': 42, 123: 555})

        # variants may add attributes
        self. assertEqual(variant_b, {'foo': 'bar', 123: 555, 'xxx': 31})

    def test_repository_triggers_logic(self):
        '''
        if a repository "triggers" a build, it will (apart from triggering the given build)
        receive a webhook configuration.
        By default, only the 'main' repository "triggers". Triggering can be explicitly
        configured using the 'trigger' attribute. Ensure that different trigger configurations
        for the same repository will be honoured properly for each variant.
        '''
        base_def = {'repo': {'name': 'main_repo', 'branch': 'dontcare', 'path': 'foo/bar'}}
        variants = {
            'variant2':
            {
                'repo': {'name': 'main_repo', 'trigger': False},
                'repos':
                [
                    {'name': 'other_repo', 'branch': 'x_branch', 'path': 'x/path', 'trigger':True}
                ]
            },
            'variant1':
            {
                'repos': [{'name': 'other_repo', 'branch': 'x_branch', 'path': 'b/path'}]
            },
            'variant3':
            {
                'repo': {'name': 'main_repo', 'trigger': False}
            },
        }
        descriptor = DefDescriptor(name='foo', base_definition=base_def, variants=variants)
        factory = DefinitionFactory(raw_definition_descriptor=descriptor)

        result = factory.create_pipeline_definition()
        variant = result.variant('variant1')

        main_repo = variant.repository('main_repo')
        other_repo = variant.repository('other_repo')

        # main_repo should "trigger" the job (and thus a webhook ought to be generated for it)
        self.assertTrue(main_repo.should_trigger())

        # non-main repos should not "trigger"
        self.assertFalse(other_repo.should_trigger())

        variant2 = result.variant('variant2')

        main_repo_v2 = variant2.repository('main_repo')
        other_repo_v2 = variant2.repository('other_repo')

        self.assertFalse(main_repo_v2.should_trigger())
        self.assertTrue(other_repo_v2.should_trigger())

        # ensure different trigger logic in resource_registry:
        # if any variant declares a repository to be triggering, this should be the result
        registry = result.resource_registry()
        main_repo_from_registry = registry.resource(main_repo.resource_identifier())

        self.assertTrue(main_repo_from_registry.should_trigger())
