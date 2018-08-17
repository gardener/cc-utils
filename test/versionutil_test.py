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

import io
import os
import stat
import tempfile
import unittest

from unittest import mock, skipIf
from unittest.mock import MagicMock

from test._test_utils import capture_out

from util import Failure
import versionutil as examinee


class VersionutilTest(unittest.TestCase):
    def test_process_version_with_input_string(self):
        with tempfile.NamedTemporaryFile(mode='w+') as tmp_output_file:
            examinee.process_version(
                version_string='8.7.6-foo',
                output_file=tmp_output_file.name,
                operation='set_prerelease_and_build',
                prerelease='foo',
                build_metadata='bar',
                build_metadata_length=2,
            )
            self.assertEqual(tmp_output_file.read(), '8.7.6-foo+ba')

    def test_process_version_passes_input_from_input_file(self):
        with tempfile.NamedTemporaryFile(mode='w') as tmp_input_file:
            with tempfile.NamedTemporaryFile(mode='w+') as tmp_output_file:
                tmp_input_file.write('3.2.1-test')
                tmp_input_file.flush()
                examinee.process_version(
                    input_file=tmp_input_file.name,
                    output_file=tmp_output_file.name,
                    operation='append_prerelease',
                    prerelease='foo',
                )
                self.assertEqual(tmp_output_file.read(), '3.2.1-test-foo')

    def test_input_from_stdin(self):
        with mock.patch('sys.stdin', new=io.StringIO('3.7.4-bar')) as mock_stdin:
            # Patch 'select.select' to fool the check for ready sys.stdin
            with mock.patch('select.select', new=MagicMock(return_value=[mock_stdin])):
                examinee.process_version(
                    operation='set_prerelease',
                    prerelease='rel',
                    output_file=os.devnull,
                )

    def test_single_output_file(self):
        with tempfile.NamedTemporaryFile(mode='w+') as temp_file:
            examinee.process_version(
                version_string='4.6.8',
                output_file=temp_file.name,
                operation='set_build_metadata',
                build_metadata='test'
            )
            self.assertEqual(temp_file.read(), '4.6.8+test')

    def test_multiple_output_files(self):
        with tempfile.NamedTemporaryFile(mode='w+') as temp_file_one:
            with tempfile.NamedTemporaryFile(mode='w+') as temp_file_two:
                examinee.process_version(
                    version_string='30.20.10-dev',
                    output_file=[temp_file_one.name, temp_file_two.name],
                    operation='set_build_metadata',
                    build_metadata='bar'
                )
                self.assertEqual(temp_file_one.read(), '30.20.10+bar')
                self.assertEqual(temp_file_two.read(), '30.20.10+bar')

    def test_fail_on_empty_stdin(self):
        with capture_out():
            with self.assertRaises(Failure) as se:
                examinee.process_version(
                    operation='set_build_metadata',
                    build_metadata='test'
                )
                self.assertNotEqual(se.exception.code, 0)

    def test_fail_on_absent_input_file(self):
        test_file_path = 'file_that_should_not_exist'
        self.assertTrue(os.path.isfile(test_file_path) is False)
        with capture_out():
            with self.assertRaises(Failure) as se:
                examinee.process_version(
                    input_file=test_file_path,
                    operation='set_build_metadata',
                    build_metadata='test'
                )
                self.assertNotEqual(se.exception.code, 0)

    def test_fail_on_output_file_with_absent_parent_directory(self):
        test_file_path = 'directory_that_should_not_exist/test_file'
        with capture_out():
            with self.assertRaises(Failure) as se:
                examinee.process_version(
                    version_string='1.1.1',
                    output_file=test_file_path,
                    operation='set_build_metadata',
                    build_metadata='test'
                )
                self.assertNotEqual(se.exception.code, 0)

    @skipIf(os.getuid() == 0, 'running as root-user, can always write')
    def test_fail_on_write_protected_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, 'foo')
            os.chmod(temp_dir, mode=stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            with self.assertRaises(PermissionError):
                examinee.process_version(
                    version_string='1.1.1',
                    output_file=test_path,
                    operation='set_build_metadata',
                    build_metadata='test'
                )

    def test_assert_no_additional_files_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            examinee.process_version(
                version_string='1.0.0',
                operation='set_prerelease',
                prerelease='dev',
                output_file=os.devnull,
            )

            self.assertEqual(len(os.listdir(temp_dir)), 0)

            test_output_one = os.path.join(temp_dir, 'foo')
            test_output_two = os.path.join(temp_dir, 'bar')

            examinee.process_version(
                version_string='1.0.0',
                operation='set_prerelease',
                output_file=[test_output_one, test_output_two],
                prerelease='dev',
            )

            temp_dir_contents = os.listdir(temp_dir)

            self.assertEqual(len(temp_dir_contents), 2)
            self.assertIn(os.path.basename(test_output_one), temp_dir_contents)
            self.assertIn(os.path.basename(test_output_two), temp_dir_contents)
