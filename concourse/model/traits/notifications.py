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

import enum
import typing

from util import not_none

from concourse.model.step import PipelineStep
from concourse.model.base import (
  AttributeSpec,
  Trait,
  TraitTransformer,
  ScriptType,
  ModelBase,
)
from model.base import (
  NamedModelElement,
)


class NotificationTriggeringPolicy(enum.Enum):
    ONLY_FIRST = 'only_first'
    ALWAYS = 'always'
    NEVER = 'never'


NOTIFICATION_CFG_ATTRS = (
    AttributeSpec.optional(
        name='triggering_policy',
        default=NotificationTriggeringPolicy.ONLY_FIRST,
        doc='when to issue the configured notifications',
        type=NotificationTriggeringPolicy,
    ),
    AttributeSpec.optional(
        name='email',
        default=True,
        doc='whether to send email notifications',
        type=bool,
    ),
    AttributeSpec.optional(
        name='inputs',
        default=['on_error_dir', 'meta'],
        doc='whether to send email notifications',
        type=typing.List[str],
    ),
)


class NotificationCfg(ModelBase):
    def __init__(self, raw_dict, *args, **kwargs):
        super().__init__(raw_dict=raw_dict, *args, **kwargs)
        self._apply_defaults(raw_dict=raw_dict)

    def _attribute_specs(self):
        return NOTIFICATION_CFG_ATTRS

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(self._attribute_specs())

    def _optional_attributes(self):
        return set(AttributeSpec.optional_attr_names(self._attribute_specs()))

    def triggering_policy(self):
        return NotificationTriggeringPolicy(self.raw['triggering_policy'])

    def should_send_email(self):
        return bool(self.raw.get('email'))

    def inputs(self):
        return self.raw.get('inputs')


NOTIFICATION_CFG_SET_ATTRS = (
    AttributeSpec.optional(
        name='on_error',
        default={'triggering_policy': 'only_first'},
        doc='configures triggering policy in case a step fails with an error',
        type=NotificationCfg,
    ),
)


class NotificationCfgSet(NamedModelElement):
    def __init__(self, name, raw_dict, *args, **kwargs):
        super().__init__(name=name, raw_dict=raw_dict, *args, **kwargs)
        self._apply_defaults(raw_dict=raw_dict)

    def _attribute_specs(self):
        return NOTIFICATION_CFG_SET_ATTRS

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(self._attribute_specs())

    def on_error(self):
        return NotificationCfg(self.raw['on_error'])

    def _children(self):
        return (self.on_error(),)


ATTRIBUTES = (
    AttributeSpec.optional(
        name='default',
        default={
            'on_error': {
                'triggering_policy': 'only_first',
                'email': True
            }
        },
        doc='the default notification cfg (more may be defined)',
        type=typing.Dict[str, NotificationCfg],
    ),
)


class NotificationsTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _attribute_specs(self):
        return ATTRIBUTES

    def _defaults_dict(self):
        return AttributeSpec.defaults_dict(ATTRIBUTES)

    def _optional_attributes(self):
        return set(self.raw.keys())

    def _children(self):
        return [NotificationCfgSet(name, raw_dict) for name, raw_dict in self.raw.items()]

    def notifications_cfg(self, cfg_name):
        return NotificationCfgSet(cfg_name, self.raw[cfg_name])

    def transformer(self):
        return NotificationsTraitTransformer(self)


class NotificationsTraitTransformer(TraitTransformer):
    name = 'notifications'

    def __init__(self, trait):
        self.trait = trait

    def process_pipeline_args(self, pipeline_args: 'JobVariant'):
        # all steps depend from us and may consume our output
        for step in pipeline_args.steps():
            step._notifications_cfg = self.trait.notifications_cfg(step.notifications_cfg_name())
