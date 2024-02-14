# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import os
import unittest
from textwrap import dedent

from tempfile import TemporaryDirectory

import model
from ci.util import Failure
from model import ConfigFactory


class ConfigFactorySmokeTestsMixin:
    '''
    smoke-tests shared by different factory creator methods (from_dict, from_cfg_dir) intended
    to ensure both deserialisation methods result in the same cfg factory.
    '''

    def test_cfg_types_parsing(self):
        types = self.examinee._cfg_types()
        self.assertEqual(types.keys(), {'a_type', 'defined_but_unused_type', 'cfg_set'})

    def test_cfg_set_parsing(self):
        singleton_set = self.examinee.cfg_set('singleton_set')
        set_with_multiple_values = self.examinee.cfg_set('set_with_multiple_values')

        self.assertIsNotNone(singleton_set)
        self.assertIsNotNone(set_with_multiple_values)

        first_element = singleton_set._cfg_element('a_type')
        second_element = set_with_multiple_values._cfg_element('a_type')

        self.assertIsNotNone(first_element)
        self.assertIsNotNone(second_element)

        self.assertTrue(isinstance(first_element, model.NamedModelElement))
        self.assertTrue(isinstance(second_element, model.NamedModelElement))

        self.assertEqual(first_element.raw, {'some_value': 123})
        self.assertEqual(second_element.raw, {'some_value': 42})

        # cfg_set is a reference to elements retrieved from the factory
        first_elem_from_fac = self.examinee._cfg_element('a_type', 'first_value_of_a')

        self.assertEqual(first_elem_from_fac.raw, first_element.raw)

    ### Tests for _cfg_element_names and _cfg_elements in ConfigFactory

    def test_cfg_element_names_should_return_all_element_names(self):
        cfg_names_set = set(self.examinee._cfg_element_names(
                cfg_type_name='a_type',
        ))
        self.assertEqual(
            cfg_names_set,
            {'first_value_of_a','second_value_of_a', 'ignored_value_of_a'}
        )

    def test_cfg_element_names_fails_on_unknown_config_type(self):
        with self.assertRaises(ValueError):
            set(self.examinee._cfg_elements(
                cfg_type_name='made_up_config_type',
            ))

    ### Tests for _cfg_element_names and _cfg_elements in ConfigurationSet

    def test_cfg_element_names_in_cfg_set_returns_empty_iterable_for_defined_but_unused_type(self):
        cfg_set = self.examinee.cfg_set('singleton_set')
        cfg_names_set = cfg_set._cfg_element_names(
                cfg_type_name='defined_but_unused_type',
        )
        for name in cfg_names_set:
            self.fail('Expected empty Iterable')

    def test_cfg_element_names_in_config_set_works_with_single_entry(self):
        # We specifically test the single-entry-case here because they are normalised
        # internally the ConfigSet
        cfg_set = self.examinee.cfg_set('singleton_set')
        cfg_names_set = cfg_set._cfg_element_names(
                cfg_type_name='a_type',
        )
        self.assertEqual(cfg_names_set, {'first_value_of_a'})

    def test_cfg_element_names_in_config_set_works_with_multiple_elements(self):
        cfg_set = self.examinee.cfg_set('set_with_multiple_values')
        cfg_names_set = cfg_set._cfg_element_names(
                cfg_type_name='a_type',
        )
        self.assertEqual(cfg_names_set, {'first_value_of_a','second_value_of_a'})

    def test_cfg_element_names_in_config_set_fails_on_unknown_config_type(self):
        cfg_set = self.examinee.cfg_set('singleton_set')
        with self.assertRaises(ValueError):
            cfg_set._cfg_element_names(cfg_type_name='made_up_config_type')

    def test_cfg_elements_in_config_set_returns_correct_element(self):
        cfg_set = self.examinee.cfg_set('singleton_set')
        cfg_elements_set = set(cfg_set._cfg_elements(
                cfg_type_name='a_type',
        ))
        self.assertEqual(len(cfg_elements_set), 1)
        cfg_elem = cfg_elements_set.pop()
        # We currently do not have a custom __eq__ method, so we explicitly
        # compare the dictionaries here
        self.assertEqual(cfg_elem.raw, {'some_value':123})


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
        defined_but_unused_type:
          src:
          - file: defined_but_unused_type_values.xxx
          model:
            cfg_type_name: defined_but_unused_type
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
        singleton_set:
            a_type: first_value_of_a
        second_set:
            a_type: second_value_of_a
        set_with_multiple_values:
            a_type:
              config_names:
              - first_value_of_a
              - second_value_of_a
              default: second_value_of_a
        ''')

        # value definitions
        self.a_type_values_file = self._file('a_type_values.xxx','''
        first_value_of_a:
            some_value: 123
        second_value_of_a:
            some_value: 42
        ignored_value_of_a:
            some_value: xxx
        ''')

        self.defined_but_unused_type_values_file = self._file(
            'defined_but_unused_type_values.xxx',
            '''
            unused:
                some_value: 7
        '''
        )

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
        with self.assertRaises(ValueError):
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
                'model': {'cfg_type_name': 'a_type', 'type': 'NamedModelElement'}
            },
            'defined_but_unused_type':
            {
                'model': {'cfg_type_name': 'defined_but_unused_type', 'type': 'NamedModelElement'}
            },
            'cfg_set':
            {
                'model': {'cfg_type_name': 'cfg_set', 'type': 'ConfigurationSet'}
            },
        }
        # config sets
        cfg_sets = {
                'singleton_set': {'a_type': 'first_value_of_a'},
                'set_with_multiple_values': {
                    'a_type': {
                          'config_names': ['first_value_of_a', 'second_value_of_a'],
                          'default': 'second_value_of_a',
                    },
                },
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
