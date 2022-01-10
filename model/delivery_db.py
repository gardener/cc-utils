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
