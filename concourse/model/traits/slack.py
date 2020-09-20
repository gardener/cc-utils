# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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
