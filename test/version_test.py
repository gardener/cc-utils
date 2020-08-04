# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import semver
import unittest

import version as examinee


class Version_find_latest_version(unittest.TestCase):
    def test_find_latest_version(self):
        versions = (semver.VersionInfo.parse(v) for v in (
                '0.0.10',
                '0.20.1',
                '2.50.100',
                '3.0.1',
                '1.0.0',
        )
        )
        result = examinee.find_latest_version(versions)
        self.assertEqual(str(result), '3.0.1')


class Version_process_version_Test(unittest.TestCase):
    # def test_invalid_version_operation(self):
    #     with self.assertRaises(ValueError):
    #         examinee.process_version(version_str='1.2.3', operation='made-up_op')

    def test_invalid_version(self):
        with self.assertRaises(ValueError):
            examinee.process_version(version_str='invalid', operation='noop')

    def test_invalid_args_prerelease_missing(self):
        with self.assertRaises(ValueError):
            examinee.process_version(version_str='1.2.3', operation='set_prerelease')

    def test_invalid_args_build_metadata_missing(self):
        with self.assertRaises(ValueError):
            examinee.process_version(version_str='1.2.3', operation='set_build_metadata')

    def test_invalid_args_build_metadata_len_less_than_zero(self):
        with self.assertRaises(ValueError):
            examinee.process_version(
            version_str='3.5.4',
            operation='set_build_metadata',
            build_metadata='someRandomString',
            build_metadata_length=-1
        )

    def test_invalid_args_append_affix_missing(self):
        with self.assertRaises(ValueError):
            examinee.process_version(
            version_str='3.5.4-foo',
            operation='append_prerelease'
        )

    def test_invalid_args_append_prerelease_missing(self):
        with self.assertRaises(ValueError):
            examinee.process_version(
            version_str='3.5.4',
            operation='append_prerelease',
            prerelease='foo'
        )

    def test_noop(self):
        parsed = examinee.process_version(version_str='1.2.3-abc', operation='noop')
        self.assertEqual(parsed, '1.2.3-abc')

    def test_set_build_metadata_length(self):
        parsed = examinee.process_version(
            version_str='1.3.5',
            operation='set_build_metadata',
            build_metadata='someRandomString',
            build_metadata_length=10
        )
        self.assertEqual(parsed, '1.3.5+someRandom')

    def test_set_prerelease_without_suffix(self):
        parsed = examinee.process_version(
            version_str='1.2.3',
            operation='set_prerelease',
            prerelease='dev'
        )
        self.assertEqual(parsed, '1.2.3-dev')

    def test_set_build_metadata_without_suffix(self):
        parsed = examinee.process_version(
            version_str='3.3.3',
            operation='set_build_metadata',
            build_metadata='build'
        )
        self.assertEqual(parsed, '3.3.3+build')

    def test_set_prerelease_with_prerelease(self):
        parsed = examinee.process_version(
            version_str='1.2.3-foo',
            operation='set_prerelease',
            prerelease='dev'
        )
        self.assertEqual(parsed, '1.2.3-dev')

    def test_set_build_metadata_with_prerelease(self):
        parsed = examinee.process_version(
            version_str='3.3.3-foo',
            operation='set_build_metadata',
            build_metadata='build'
        )
        self.assertEqual(parsed, '3.3.3+build')

    def test_set_prerelease_with_build_metadata(self):
        parsed = examinee.process_version(
            version_str='1.2.3+foo',
            operation='set_prerelease',
            prerelease='dev'
        )
        self.assertEqual(parsed, '1.2.3-dev')

    def test_set_build_metadata_with_build_metadata(self):
        parsed = examinee.process_version(
            version_str='3.3.3+foo',
            operation='set_build_metadata',
            build_metadata='build'
        )
        self.assertEqual(parsed, '3.3.3+build')

    def test_set_prerelease_and_build_metadata_without_suffix(self):
        parsed = examinee.process_version(
            version_str='6.6.6',
            operation='set_prerelease_and_build',
            prerelease='dev',
            build_metadata='build'
        )
        self.assertEqual(parsed, '6.6.6-dev+build')

    def test_set_prerelease_and_build_metadata_with_prerelease(self):
        parsed = examinee.process_version(
            version_str='4.3.2-foo',
            operation='set_prerelease_and_build',
            prerelease='dev',
            build_metadata='build'
        )
        self.assertEqual(parsed, '4.3.2-dev+build')

    def test_set_prerelease_and_build_metadata_with_build(self):
        parsed = examinee.process_version(
            version_str='9.6.3+bar',
            operation='set_prerelease_and_build',
            prerelease='dev',
            build_metadata='build'
        )
        self.assertEqual(parsed, '9.6.3-dev+build')

    def test_set_prerelease_and_build_metadata_with_prerelease_and_build(self):
        parsed = examinee.process_version(
            version_str='8.1.5-bar+baz',
            operation='set_prerelease_and_build',
            prerelease='dev',
            build_metadata='build'
        )
        self.assertEqual(parsed, '8.1.5-dev+build')

    def test_append_prerelease(self):
        parsed = examinee.process_version(
            version_str='4.9.16-foo',
            operation='append_prerelease',
            prerelease='bar',
        )
        self.assertEqual(parsed, '4.9.16-foo-bar')

    def test_append_prerelease_with_build_metadata(self):
        parsed = examinee.process_version(
            version_str='3.1.4-foo+bar',
            operation='append_prerelease',
            prerelease='baz',
        )
        self.assertEqual(parsed, '3.1.4-foo-baz+bar')

    def test_set_verbatim_with_verbatim_version(self):
        parsed = examinee.process_version(
            version_str='3.1.4-foo+bar',
            operation='set_verbatim',
            verbatim_version='master',
        )
        self.assertEqual(parsed, 'master')

    def test_set_verbatim_without_verbatim_version(self):
        with self.assertRaises(ValueError):
            examinee.process_version(
                version_str='3.1.4-foo',
                operation='set_verbatim',
                prerelease='baz'
            )

    def test_bump_major(self):
        parsed = examinee.process_version(version_str='2.4.6', operation='bump_major')
        self.assertEqual(parsed, '3.0.0')

    def test_bump_minor(self):
        parsed = examinee.process_version(version_str='2.4.6', operation='bump_minor')
        self.assertEqual(parsed, '2.5.0')

    def test_bump_patch(self):
        parsed = examinee.process_version(version_str='2.4.6', operation='bump_patch')
        self.assertEqual(parsed, '2.4.7')
