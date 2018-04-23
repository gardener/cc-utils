import unittest

from model import ModelValidationError
from concourse.pipelines.factory import (
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


