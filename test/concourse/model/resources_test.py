import unittest

from concourse.model.resources import ResourceIdentifier


class ResourceIdentifierTest(unittest.TestCase):
    def test_ctor_arg_validation(self):
        examinee = ResourceIdentifier

        # valid invocations
        examinee(type_name='foo', base_name='bar', qualifier='qual', logical_name='x')
        examinee(type_name='foo', base_name='bar', qualifier='qual')
        examinee(type_name='foo', base_name='bar')

        with self.assertRaises(ValueError):
            examinee(type_name=None, base_name='x')

        with self.assertRaises(ValueError):
            examinee(type_name='foo', base_name=None)

    def test_ctor(self):
        examinee = ResourceIdentifier
        result = examinee(type_name='foo', base_name='bar', qualifier='qual', logical_name='x')

        self.assertEqual(result.type_name(), 'foo')
        self.assertEqual(result.base_name(), 'bar')
        self.assertEqual(result.logical_name(), 'x')
        self.assertEqual(result.name(), 'foo-bar-qual')

        result = examinee(type_name='foo', base_name='bar', qualifier=None)

        self.assertEqual(result.name(), 'foo-bar')

    def test_equal(self):
        examinee = ResourceIdentifier

        left = examinee(type_name='type1', base_name='base1', qualifier='qual1')
        right = examinee(type_name='type1', base_name='base1', qualifier='qual1')

        self.assertEqual(left, right)

        self.assertNotEqual(left, examinee(type_name='type2', base_name='base1', qualifier='qual1'))
        self.assertNotEqual(left, examinee(type_name='type1', base_name='base2', qualifier='qual1'))
        self.assertNotEqual(left, examinee(type_name='type1', base_name='base1', qualifier='qual2'))
        self.assertNotEqual(left, examinee(type_name='type1', base_name='base1'))
