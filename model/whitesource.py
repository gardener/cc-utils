# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class WhitesourceCredentials(BasicCredentials):
    def user(self):
        return self.raw.get('user')

    def user_key(self):
        return self.raw.get('user_key')


class WhitesourceConfig(NamedModelElement):
    def wss_endpoint(self):
        return self.raw.get('wss_endpoint')

    def api_key(self):
        return self.raw.get('api_key')

    def extension_endpoint(self):
        return self.raw.get('extension_endpoint')

    def credentials(self):
        return WhitesourceCredentials(self.raw.get('credentials'))

    def _required_attributes(self):
        return 'credentials', 'wss_endpoint', 'api_key', 'extension_endpoint'
