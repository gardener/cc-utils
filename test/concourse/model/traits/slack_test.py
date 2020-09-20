# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import unittest

from model.base import ModelValidationError
from concourse.model.traits.slack import SlackTrait


class SlackTraitTest(unittest.TestCase):
    def test_slack_trait_validation(self):
        channel_cfgs = {
            'foo': {
                'channel_name':'foo',
                'slack_cfg_name':'bar'
            }
        }

        examinee = SlackTrait

        # valid invocations
        examinee(
            'my_slack_trait',
            'my_variant_name',
            raw_dict={
                'default_channel': 'foo',
                'channel_cfgs': channel_cfgs
            }
        ).validate()

        with self.assertRaises(ModelValidationError):
            examinee(
                'my_slack_trait',
                'my_variant_name',
                raw_dict={
                    'default_channel': 'not_existing',
                    'channel_cfgs': channel_cfgs
                }
            ).validate()
