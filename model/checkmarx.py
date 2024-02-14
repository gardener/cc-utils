# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class CheckmarxCredentials(BasicCredentials):
    def client_id(self):
        return self.raw.get('client_id')

    def client_secret(self):
        return self.raw.get('client_secret')

    def domain(self):
        return self.raw.get('domain')

    def scope(self):
        return self.raw.get('scope', 'sast_rest_api')

    def qualified_username(self):
        return f'{self.domain()}\\{self.username()}'


class CheckmarxConfig(NamedModelElement):
    def base_url(self):
        return self.raw.get('base_url')

    def team_id(self):
        return self.raw.get('team_id')

    def credentials(self):
        return CheckmarxCredentials(self.raw.get('credentials'))

    def _required_attributes(self):
        return 'credentials', 'base_url'
