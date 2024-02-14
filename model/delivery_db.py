# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class PostgresCredentials(BasicCredentials):
    def username(self):
        return self.raw.get('username')

    def password(self):
        return self.raw.get('password')


class DeliveryDbConfig(NamedModelElement):
    def credentials(self):
        return PostgresCredentials(self.raw.get('credentials'))

    def hostname(self):
        return self.raw.get('hostname')

    def port(self):
        return self.raw.get('port')

    def helm_values(self):
        return self.raw.get('helm_values')

    def db_type(self):
        return self.raw.get('db_type', 'postgresql')

    def as_url(self):
        creds = self.credentials()
        if self.credentials():
            auth_str = f'{creds.username()}:{creds.passwd()}@'
        else:
            auth_str = ''

        return f'{self.db_type()}://{auth_str}{self.hostname()}:{self.port()}'

    def _defaults_dict(self):
        return {
            'db_type': 'postgresql',
        }

    def _required_attributes(self):
        return (
            'credentials',
        )
