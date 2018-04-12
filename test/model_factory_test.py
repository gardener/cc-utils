# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import os
import unittest
from textwrap import dedent

from tempfile import TemporaryDirectory

import model
from util import Failure
from model import ConfigFactory

class ConfigFactorySmokeTestsMixin(object):
    '''
    smoke-tests shared by different factory creator methods (from_dict, from_cfg_dir) intended
    to ensure both deserialisation methods result in the same cfg factory.
    '''
    def test_cfg_types_parsing(self):
        types = self.examinee._cfg_types()
        self.assertEqual(types.keys(), {'a_type', 'cfg_set'})

    def test_cfg_set_parsing(self):
        first_set = self.examinee.cfg_set('first_set')
        second_set = self.examinee.cfg_set('second_set')

        self.assertIsNotNone(first_set)
        self.assertIsNotNone(second_set)

        first_element = first_set._cfg_element('a_type')
        second_element = second_set._cfg_element('a_type')

        self.assertIsNotNone(first_element)
        self.assertIsNotNone(second_element)

        self.assertTrue(isinstance(first_element, model.NamedModelElement))
        self.assertTrue(isinstance(second_element, model.NamedModelElement))

        self.assertEquals(first_element.raw, {'some_value': 123})
        self.assertEquals(second_element.raw, {'some_value': 42})

        # cfg_set is a reference to elements retrieved from the factory
        first_elem_from_fac = self.examinee._cfg_element('a_type', 'first_value_of_a')

        self.assertEqual(first_elem_from_fac.raw, first_element.raw)


class ConfigFactoryCfgDirDeserialisationTest(unittest.TestCase, ConfigFactorySmokeTestsMixin):
    '''
    tests ensuring ConfigFactory's from_cfg_dir method properly creates a cfg factory
    from a given configuration directory (this is the case when consuming a copy of
    "kubernetes/cc-config" as input)
    '''
    def setUp(self):
        self.tmpdir = TemporaryDirectory()

        # type definition
        self.types_file = self._file('types','''
        a_type:
          src:
          - file: a_type_values.xxx
          model:
            cfg_type_name: a_type
            type: NamedModelElement
        cfg_set:
          src:
          - file: configs
          model:
            cfg_type_name: cfg_set
            type: ConfigurationSet
        ''')

        # cfg_set definitions
        self.configs_file = self._file('configs', '''
        first_set:
            a_type: first_value_of_a
        second_set:
            a_type: second_value_of_a
        ''')

        # value definitions
        self.values_file = self._file('a_type_values.xxx','''
        first_value_of_a:
            some_value: 123
        second_value_of_a:
            some_value: 42
        ignored_value_of_a:
            some_value: xxx
        ''')

        self.examinee = ConfigFactory.from_cfg_dir(
            cfg_dir=self.tmpdir.name,
            cfg_types_file=self.types_file
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def _file(self, name, contents):
        filename = os.path.join(self.tmpdir.name, name)
        with open(filename, 'w') as f:
            f.write(dedent(contents))
        return filename

    def test_absent_directory_causes_failure(self):
        with self.assertRaises(Failure):
            ConfigFactory.from_cfg_dir(cfg_dir='should not exist')

    def test_absent_cfg_types_file_causes_failure(self):
        with self.assertRaises(FileNotFoundError):
            ConfigFactory.from_cfg_dir(
                cfg_dir=self.tmpdir.name,
                cfg_types_file='another absent file'
            )


class ConfigFactoryDictDeserialisationTest(unittest.TestCase, ConfigFactorySmokeTestsMixin):
    '''
    tests ensuring that ConfigFactory's from_dict method properly creates a cfg factory
    from a dictionary (this is the case when consuming a previously serialised copy of a
    configuration that was previously serialised using model.ConfigSetSerialiser).
    '''
    def setUp(self):
        # type definitions
        types = {
            'a_type':
            {
                'model': { 'cfg_type_name': 'a_type', 'type': 'NamedModelElement' }
            },
            'cfg_set':
            {
                'model': { 'cfg_type_name': 'cfg_set', 'type': 'ConfigurationSet' }
            }
        }
        # config sets
        cfg_sets = {
                'first_set': {'a_type': 'first_value_of_a'},
                'second_set': {'a_type': 'second_value_of_a'}
        }
        # value definitions
        values = {
                'first_value_of_a': {'some_value': 123},
                'second_value_of_a': {'some_value': 42},
                'ignored_value_of_a': {'some_value': 'xxx'},
        }

        raw = {'cfg_types': types, 'cfg_set': cfg_sets, 'a_type': values}

        self.examinee = ConfigFactory.from_dict(raw)

    def test_from_dict_fails_on_none(self):
        with self.assertRaises(Failure):
            ConfigFactory.from_dict(None)

    def test_from_dict_fails_on_missing_cfg_types(self):
        with self.assertRaises(ValueError):
            ConfigFactory.from_dict({})


