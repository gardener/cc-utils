# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
import pathlib

from test._test_utils import capture_out

from util import Failure
import util as examinee


class UtilTest(unittest.TestCase):
    def test_info(self):
        with capture_out() as (stdout, stderr):
            examinee.info(msg='test abc')
        self.assertEqual('INFO: test abc', stdout.getvalue().strip())
        self.assertTrue(len(stderr.getvalue()) == 0)

    def test_info_with_quiet(self):
        class Args(object):
            pass
        args = Args()
        args.quiet = True
        import ctx
        ctx.args = args

        with capture_out() as (stdout, stderr):
            examinee.info(msg='should not be printed')

        self.assertTrue(len(stdout.getvalue()) == 0)
        self.assertTrue(len(stderr.getvalue()) == 0)

    def test_fail(self):
        with capture_out() as (stdout, stderr):
            with self.assertRaises(Failure):
                examinee.fail(msg='foo bar')

        self.assertEqual('ERROR: foo bar', stdout.getvalue().strip())
        self.assertTrue(len(stderr.getvalue()) == 0)

    def test_not_empty(self):
        result = examinee.not_empty('foo')

        self.assertEqual('foo', result)

        forbidden = ['', None, [], ()]

        for value in forbidden:
            with capture_out() as (stdout, stderr):
                with self.assertRaises(Failure):
                    examinee.not_empty(value)
            self.assertIn('must not be empty', stdout.getvalue().strip())
            self.assertTrue(len(stderr.getvalue()) == 0)

    def test_existing_file(self):
        import sys
        existing_file = sys.executable

        result = examinee.existing_file(existing_file)

        self.assertEqual(existing_file, result)

        with capture_out() as (stdout, stderr):
            with self.assertRaises(Failure):
                examinee.existing_file('no such file, I hope')
        self.assertIn('not an existing file', stdout.getvalue().strip())
        self.assertTrue(len(stderr.getvalue()) == 0)

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

