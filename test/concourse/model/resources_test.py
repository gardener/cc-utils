import unittest

from concourse.model.resources import ResourceIdentifier


class ResourceIdentifierTest(unittest.TestCase):
    def test_ctor_arg_validation(self):
        examinee = ResourceIdentifier

        # valid invocations
        examinee(
            type_name='foo',
            base_name='bar',
            branch_name='baz',
            qualifier='qual',
            logical_name='x',
        )
        examinee(type_name='foo', base_name='bar', branch_name='baz', qualifier='qual')
        examinee(type_name='foo', base_name='bar', branch_name='baz',)

        with self.assertRaises(ValueError):
            examinee(type_name=None, base_name='bar', branch_name='baz')

        with self.assertRaises(ValueError):
            examinee(type_name='foo', base_name=None, branch_name='baz')

        with self.assertRaises(ValueError):
            examinee(type_name='foo', base_name='bar', branch_name=None)

    def test_ctor(self):
        examinee = ResourceIdentifier
        result = examinee(
            type_name='foo',
            base_name='bar',
            branch_name='baz',
            qualifier='qual',
            logical_name='x'
        )

        self.assertEqual(result.type_name(), 'foo')
        self.assertEqual(result.base_name(), 'bar')
        self.assertEqual(result.branch_name(), 'baz')
        self.assertEqual(result.logical_name(), 'x')
        self.assertEqual(result.name(), 'foo-bar-baz-qual')

        result = examinee(type_name='foo', base_name='bar', branch_name='baz', qualifier=None)

        self.assertEqual(result.name(), 'foo-bar-baz')

    def test_equal(self):
        examinee = ResourceIdentifier

        left = examinee(
            type_name='type1', base_name='base1', branch_name='branch1', qualifier='qual1',
            )
        right = examinee(
            type_name='type1', base_name='base1', branch_name='branch1', qualifier='qual1',
            )

        self.assertEqual(left, right)

        # differs in type_name
        self.assertNotEqual(
            left,
            examinee(
                type_name='type2',
                base_name='base1',
                branch_name='branch1',
                qualifier='qual1',
            ),
        )
        # differs in base_name
        self.assertNotEqual(
            left,
            examinee(
                type_name='type1',
                base_name='base2',
                branch_name='branch1',
                qualifier='qual1',
            ),
        )
        # differs in branch_name
        self.assertNotEqual(
            left,
            examinee(
                type_name='type1',
                base_name='base1',
                branch_name='branch2',
                qualifier='qual1',
            ),
        )
        # differs in qualifier
        self.assertNotEqual(
            left,
            examinee(
                type_name='type1',
                base_name='base1',
                branch_name='branch1',
                qualifier='qual2',
            ),
        )
        # absent qualifier
        self.assertNotEqual(
            left,
            examinee(
                type_name='type1',
                base_name='base1',
                branch_name='branch1',
            ),
        )
