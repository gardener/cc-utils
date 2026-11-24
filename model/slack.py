# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    NamedModelElement,
)


class SlackConfig(NamedModelElement):
    def api_token(self):
        return self.raw.get('api_token')

    def signing_secret(self):
        return self.raw.get('signing_secret')

    def _required_attributes(self):
        return ['api_token']
