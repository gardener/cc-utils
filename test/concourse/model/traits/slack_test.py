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
