# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import unittest
import pathlib
import textwrap
import pytest
import yaml

from test._test_utils import capture_out

from ci.util import Failure
import ci.util as examinee
import ci.util


def test_info():
    with capture_out() as (stdout, stderr):
        examinee.info(msg='test abc')
    assert 'INFO: test abc' == stdout.getvalue().strip()
    assert len(stderr.getvalue()) == 0


def test_info_with_quiet():
    class Args:
        pass
    args = Args()
    args.quiet = True
    import ctx
    ctx.args = args

    with capture_out() as (stdout, stderr):
        examinee.info(msg='should not be printed')

    assert len(stdout.getvalue()) == 0
    assert len(stderr.getvalue()) == 0


def test_fail():
    with capture_out() as (stdout, stderr):
        with pytest.raises(Failure):
            examinee.fail(msg='foo bar')

    assert 'ERROR: foo bar' == stderr.getvalue().strip()
    assert len(stdout.getvalue()) == 0


def test_success():
    with capture_out() as (stdout, stderr):
        examinee.success('xxx')

    assert 'SUCCESS: xxx' == stdout.getvalue().strip()
    assert len(stderr.getvalue()) == 0


class UtilTest(unittest.TestCase):
    def test_not_empty(self):
        result = examinee.not_empty('foo')

        self.assertEqual('foo', result)

        forbidden = ['', None, [], ()]

        for value in forbidden:
            with capture_out() as (stdout, stderr):
                with self.assertRaises(Failure):
                    examinee.not_empty(value)
            self.assertIn('must not be empty', stderr.getvalue().strip())
            self.assertTrue(len(stdout.getvalue()) == 0)

    def test_existing_file(self):
        import sys
        existing_file = sys.executable

        result = examinee.existing_file(existing_file)

        self.assertEqual(existing_file, result)

        with capture_out() as (stdout, stderr):
            with self.assertRaises(Failure):
                examinee.existing_file('no such file, I hope')
        self.assertIn('not an existing file', stderr.getvalue().strip())
        self.assertTrue(len(stdout.getvalue()) == 0)

        # should also work with pathlib.Path
        existing_file = pathlib.Path(existing_file)
        self.assertEqual(examinee.existing_file(existing_file), existing_file)

    def test_urljoin(self):
        self.assertEqual('foo/bar', examinee.urljoin('foo/bar'))
        # leading/trailing slashes should be preserved
        self.assertEqual('//foo/bar//', examinee.urljoin('//foo/bar//'))

        self.assertEqual(
            'xxx://foo.bar/abc/def/',
            examinee.urljoin('xxx://foo.bar', 'abc', 'def/')
        )

        # leading/trailing slashes for "inner" parts should be slurped
        self.assertEqual(
            'gnu://foo.bar/abc/def/',
            examinee.urljoin('gnu://foo.bar/', '/abc/', '/def/')
        )

    def test_merge_dicts_simple(self):
        left = {1: {2: 3}}
        right = {1: {4: 5}, 6: 7}

        merged = examinee.merge_dicts(left, right)

        self.assertEqual(
            merged,
            {
                1: {2: 3, 4: 5},
                6: 7,
            }
        )

    def test_merge_dicts_with_merge_retains_order(self):
        left = {1: [3, 1, 0]}
        right = {1: [1, 2, 4]}

        merged = examinee.merge_dicts(left, right, list_semantics='merge')

        self.assertEqual(
            merged,
            {1: [3, 1, 0, 2, 4]},
        )

    def test_merge_dicts_does_not_modify_args(self):
        from copy import deepcopy
        first = {1: {2: 3}}
        second = {1: {4: 5}, 6: 7}
        first_arg = deepcopy(first)
        second_arg = deepcopy(second)

        merged = examinee.merge_dicts(first_arg, second_arg)

        self.assertEqual(
            merged,
            {
                1: {2: 3, 4: 5},
                6: 7,
            }
        )
        self.assertEqual(first, first_arg)
        self.assertEqual(second, second_arg)

    def test_merge_dicts_three_way_merge(self):
        first = {1: [3, 1, 0]}
        second = {1: [1, 2, 4]}
        third = {1: [1, 2, 5], 2: [1, 2, 3]}

        merged = examinee.merge_dicts(first, second, third, list_semantics='merge')

        self.assertEqual(
            merged,
            {
                1: [3, 1, 0, 2, 4, 5],
                2: [1, 2, 3],
            }
        )


def test_count_elements():
    count = ci.util._count_elements

    # trivial cases: non-iterable elements
    assert count(1) == 1
    assert count('foo') == 1
    assert count(object()) == 1

    # non-nested lists
    assert count([]) == 0
    assert count([1]) == 1
    assert count([1, 'a']) == 2

    # nested lists
    assert count([[1,2], [3,4]]) == 4
    assert count([[1,2], [['x'], 'y']]) == 4

    # shallow dicts
    assert count({1: 'foo', 2: 'bar'}) == 2
    assert count({'list': [1,2,3,4]}) == 4

    # nested dicts
    assert count({1: {2: 4}}) == 1

    # bomb
    yaml_bomb = textwrap.dedent('''
a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]
e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]
f: &f [*e,*e,*e,*e,*e,*e,*e,*e,*e]
g: &g [*f,*f,*f,*f,*f,*f,*f,*f,*f]
h: &h [*g,*g,*g,*g,*g,*g,*g,*g,*g]
i: &i [*h,*h,*h,*h,*h,*h,*h,*h,*h]
    ''')
    parsed = yaml.safe_load(yaml_bomb)

    with pytest.raises(ValueError):
        count(parsed)
