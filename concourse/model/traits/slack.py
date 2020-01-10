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

import typing

from concourse.model.base import (
  AttributeSpec,
  Trait,
  TraitTransformer,
  ModelBase,
  ModelValidationError,
)
from concourse.model.job import (
    JobVariant,
)

CHANNEL_CFG_ATTRS = (
    AttributeSpec.required(
        name='channel_name',
        doc='the slack channel name',
        type=str,
    ),
    AttributeSpec.required(
        name='slack_cfg_name',
        doc='slack_cfg name (see cc-config)',
        type=str,
    ),
)


class ChannelConfig(ModelBase):
    @classmethod
    def _attribute_specs(cls):
        return CHANNEL_CFG_ATTRS

    def channel_name(self):
        return self.raw.get('channel_name')

    def slack_cfg_name(self):
        return self.raw.get('slack_cfg_name')

    def _required_attributes(self):
        return {
            'channel_name',
            'slack_cfg_name',
        }


ATTRIBUTES = (
    AttributeSpec.required(
        name='channel_cfgs',
        doc='the slack channel configuration to use',
        type=typing.Dict[str, ChannelConfig],
    ),
    AttributeSpec.required(
        name='default_channel',
        doc='the default channel config',
    ),
)


class SlackTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _children(self):
       return self.channel_cfgs().values()

    def channel_cfgs(self):
        return {
            name: ChannelConfig(raw_dict=v)
            for name, v in self.raw.get('channel_cfgs').items()
        }

    def default_channel(self):
        return self.raw.get('default_channel')

    def transformer(self):
        return SlackTraitTransformer()

    def validate(self):
        super().validate()
        default_channel = self.default_channel()
        if default_channel not in self.channel_cfgs():
            raise ModelValidationError(
                'there is no element in channel_cfgs with name {name}'.format(
                    name=default_channel,
                )
            )


class SlackTraitTransformer(TraitTransformer):
    name = 'slack'

    def process_pipeline_args(self, pipeline_args: JobVariant):
        pass
