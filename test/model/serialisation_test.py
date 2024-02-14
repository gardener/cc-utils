# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import unittest
import json

from model import ConfigSetSerialiser as CSS, ConfigFactory
from model.base import ConfigElementNotFoundError


def simple_cfg_dict():
    types = {
        'a_type':
        {
            'model': {'cfg_type_name': 'a_type', 'type': 'NamedModelElement'}
        },
        'cfg_set':
        {
            'model': {'cfg_type_name': 'cfg_set', 'type': 'ConfigurationSet'}
        }
    }
    # config sets
    cfg_sets = {
            'first_set': {'a_type': 'first_value_of_a'},
            'second_set': {'a_type': 'second_value_of_a'},
            'set_with_two_of_a_kind':
            {
                'a_type':
                    {
                        'config_names': ['first_value_of_a', 'second_value_of_a'],
                        'default': 'second_value_of_a'
                    }
            },
    }
    # value definitions
    values = {
            'first_value_of_a': {'some_value': 123},
            'second_value_of_a': {'some_value': 42},
            'ignored_value_of_a': {'some_value': 'xxx'},
    }

    return {'cfg_types': types, 'cfg_set': cfg_sets, 'a_type': values}


class ConfigSetSerialiserTest(unittest.TestCase):
    def setUp(self):
        self.factory = ConfigFactory.from_dict(simple_cfg_dict())
        self.first_cfg_set = self.factory.cfg_set('first_set')
        self.second_cfg_set = self.factory.cfg_set('second_set')
        self.set_with_two_of_a_kind = self.factory.cfg_set('set_with_two_of_a_kind')

    def exercise(self, cfg_sets):
        examinee = CSS(cfg_sets=cfg_sets, cfg_factory=self.factory)
        return json.loads(examinee.serialise(output_format='json'))

    def deserialise(self, raw_dict):
        return ConfigFactory.from_dict(raw_dict)

    def test_serialise_empty_set(self):
        self.assertEqual(self.exercise({}), {})

    def test_serialise_one_set(self):
        result = self.exercise({self.first_cfg_set})

        # parse result again using cfg-factory so we do not have to make assumptions
        # about serialisation format
        deserialised = self.deserialise(result)

        first_cfg_set = deserialised.cfg_set('first_set')
        self.assertEqual(first_cfg_set.raw, self.first_cfg_set.raw)

        with self.assertRaises(ValueError):
            # second_set must not have been included
            deserialised.cfg_set('second_set')

        with self.assertRaises(ConfigElementNotFoundError):
            # only explicitly referenced values must be included
            deserialised._cfg_element('a_type', 'ignored_value_of_a')

    def test_serialise_two_sets(self):
        result = self.exercise({self.first_cfg_set, self.second_cfg_set})
        deserialised = self.deserialise(result)

        first_cfg_set = deserialised.cfg_set('first_set')
        second_cfg_set = deserialised.cfg_set('second_set')

        self.assertEqual(first_cfg_set.raw, self.first_cfg_set.raw)
        self.assertEqual(second_cfg_set.raw, self.second_cfg_set.raw)

        with self.assertRaises(ConfigElementNotFoundError):
            # only explicitly referenced values must be included
            deserialised._cfg_element('a_type', 'ignored_value_of_a')

    def test_serialise_set_with_two_of_a_kind(self):
        result = self.exercise({self.set_with_two_of_a_kind})
        deserialised = self.deserialise(result)

        two_of_a_kind_set = deserialised.cfg_set('set_with_two_of_a_kind')

        # test that the configured default value is returned
        second_value = two_of_a_kind_set._cfg_element('a_type')
        self.assertEqual(second_value.raw['some_value'], 42)

        # ensure first_value is also contained in serialisation result (returned from factory)
        first_value = deserialised._cfg_element('a_type', 'first_value_of_a')
        self.assertEqual(first_value.raw['some_value'], 123)

        # ensure the same value is also returned from the cfg_set
        first_value_from_cfg_set = two_of_a_kind_set._cfg_element('a_type', 'first_value_of_a')
        self.assertEqual(first_value.raw, first_value_from_cfg_set.raw)
