# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import (
    NamedModelElement,
)


class SlackConfig(NamedModelElement):
    def api_token(self):
        return self.raw.get('api_token')

    def oncall_app_signing_secret(self):
        return self.raw.get('oncall_app_signing_secret')

    def oncall_app_bot_token(self):
        return self.raw.get('oncall_app_bot_token')

    def _required_attributes(self):
        return ['api_token']
