# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import typing

from concourse.model.base import (
  AttributeSpec,
  Trait,
  TraitTransformer,
  ModelBase,
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
    AttributeSpec.optional(
        name='post_full_release_notes',
        default=False,
        doc='''
        if the slack trait is used in conjunction with the release trait, specifies whether full
        release notes (containing sub-components' release notes and OCI resources as well) or only
        local release notes should be posted
        ''',
        type=bool,
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

    def post_full_release_notes(self):
        return self.raw.get('post_full_release_notes')

    def _required_attributes(self):
        return {
            'channel_name',
            'slack_cfg_name',
        }


ATTRIBUTES = (
    AttributeSpec.required(
        name='channel_cfgs',
        doc='the slack channel configurations to use',
        type=typing.Union[typing.List[ChannelConfig], typing.Dict[str, ChannelConfig]],
    ),
    AttributeSpec.deprecated(
        name='default_channel',
        doc='**deprecated**',
        type=str,
    ),
)


class SlackTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _children(self):
       return self.channel_cfgs()

    def channel_cfgs(self):
        channel_cfgs = self.raw.get('channel_cfgs')
        if isinstance(channel_cfgs, list):
            return [ChannelConfig(raw_dict=v) for v in channel_cfgs]
        else:
            return [ChannelConfig(raw_dict=v) for v in channel_cfgs.values()]

    def transformer(self):
        return SlackTraitTransformer()

    def validate(self):
        super().validate()


class SlackTraitTransformer(TraitTransformer):
    name = 'slack'

    def process_pipeline_args(self, pipeline_args: JobVariant):
        pass
