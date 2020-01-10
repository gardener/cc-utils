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

import enum
import typing

from concourse.model.base import (
  AttributeSpec,
  AttribSpecMixin,
  Trait,
  TraitTransformer,
  ModelBase,
  normalise_to_dict,
)
from concourse.model.job import (
    JobVariant,
)
from model.base import (
  NamedModelElement,
)


class NotificationTriggeringPolicy(AttribSpecMixin, enum.Enum):
    ONLY_FIRST = 'only_first'
    ALWAYS = 'always'
    NEVER = 'never'

    @classmethod
    def _attribute_specs(cls):
        return (
            AttributeSpec.optional(
                name=cls.ONLY_FIRST.value,
                default=True,
                doc='notify on first error only',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.ALWAYS.value,
                default=False,
                doc='notify on every error',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.NEVER.value,
                default=False,
                doc='notify never in case of errors',
                type=str,
            ),
        )


class NotificationRecipients(AttribSpecMixin, enum.Enum):
    EMAIL_ADDRESSES = 'email_addresses'
    COMMITTERS = 'committers'
    COMPONENT_DIFF_OWNERS = 'component_diff_owners'
    CODEOWNERS = 'codeowners'

    @classmethod
    def _attribute_specs(cls):
        return (
            AttributeSpec.optional(
                name=cls.COMMITTERS.value,
                default=None,
                doc='notify committers of the last commit',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.EMAIL_ADDRESSES.value,
                default=None,
                doc='''
                notifiy specific email addresses

                Example:

                .. code-block:: yaml

                    recipients:
                        - email_addresses:
                            - foo.bar@mycloud.com
                            - bar.buzz@mycloud.com
                ''',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.COMPONENT_DIFF_OWNERS.value,
                default=None,
                doc='notify the codeowners of a component. CODEOWNERS file must exist',
                type=str,
            ),
            AttributeSpec.optional(
                name=cls.CODEOWNERS.value,
                default=None,
                doc='notify the codeowners of the repository. CODEOWNERS file must exist',
                type=str,
            ),
        )


NOTIFICATION_CFG_ATTRS = (
    AttributeSpec.optional(
        name='triggering_policy',
        default=NotificationTriggeringPolicy.ONLY_FIRST.value,
        doc='when to issue the configured notifications. Possible values see below',
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
        default=['on_error_dir'],
        doc='configures the inputs that are made available to the notification',
        type=typing.List[str],
    ),
    AttributeSpec.optional(
        name='recipients',
        default=NotificationRecipients.COMMITTERS.value,
        doc='whom to notify. Possible values see blow.',
        type=NotificationRecipients,
    ),
    AttributeSpec.optional(
        name='cfg_callback',
        default=['on_error_dir'],
        doc='''
        an optional callback (relative to main repository root). Called as subprocess with
        an environment variables:

        - `REPO_ROOT`: absolute path to main repository
        - `NOTIFY_CFG_OUT`: absolute path to write notify.cfg to
        ''',
        type=str,
    ),
)


class NotificationCfg(ModelBase):
    def __init__(self, raw_dict, *args, **kwargs):
        super().__init__(raw_dict=raw_dict, *args, **kwargs)
        self._apply_defaults(raw_dict=raw_dict)
        self.raw['recipients'] = normalise_to_dict(self.recipients())

    @classmethod
    def _attribute_specs(cls):
        return NOTIFICATION_CFG_ATTRS

    def triggering_policy(self):
        return NotificationTriggeringPolicy(self.raw['triggering_policy'])

    def should_send_email(self):
        return bool(self.raw.get('email'))

    def inputs(self):
        return self.raw.get('inputs')

    def recipients(self):
        return self.raw.get('recipients')

    def cfg_callback(self):
        return self.raw.get('cfg_callback')


NOTIFICATION_CFG_SET_ATTRS = (
    AttributeSpec.optional(
        name='on_error',
        default={'triggering_policy': 'only_first'},
        doc='configures triggering policy in case a step fails with an error',
        type=NotificationCfg,
    ),
)


class NotificationCfgSet(NamedModelElement, AttribSpecMixin):
    def __init__(self, name, raw_dict, *args, **kwargs):
        super().__init__(name=name, raw_dict=raw_dict, *args, **kwargs)
        self._apply_defaults(raw_dict=raw_dict)

    @classmethod
    def _attribute_specs(cls):
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
        type=NotificationCfgSet,
    ),
)


class NotificationsTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    def _known_attributes(self):
        return self.raw.keys()

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

    def process_pipeline_args(self, pipeline_args: JobVariant):
        # all steps depend from us and may consume our output
        for step in pipeline_args.steps():
            step._notifications_cfg = self.trait.notifications_cfg(step.notifications_cfg_name())
