# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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
import functools
import random

from urllib.parse import urlparse

from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelValidationError,
)


class Protocol(enum.Enum):
    SSH = 'ssh'
    HTTPS = 'https'


class GithubConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def purpose_labels(self):
        return set(self.raw.get('purpose_labels', ()))

    def ssh_url(self):
        if Protocol.SSH not in self.available_protocols():
            raise RuntimeError(f"SSH protocol is not available for Github config '{self.name()}'")
        return self.raw.get('sshUrl')

    def http_url(self):
        if Protocol.HTTPS not in self.available_protocols():
            raise RuntimeError(f"HTTPS protocol is not available for Github config '{self.name()}'")
        return self.raw.get('httpUrl')

    def api_url(self):
        return self.raw.get('apiUrl')

    def tls_validation(self):
        return not self.raw.get('disable_tls_validation')

    def webhook_secret(self):
        return self.raw.get('webhook_token')

    def preferred_protocol(self):
        return self.available_protocols()[0]

    def available_protocols(self):
        '''Return available git protocols, in order of preference (most preferred protcol first)
        '''
        return [Protocol(value) for value in self.raw.get('available_protocols')]

    @functools.lru_cache()
    def credentials(self, technical_user_name: str = None):
        if self.raw.get('technical_users'):
            technical_users = [
                GithubCredentials(user) for user in self.raw.get('technical_users')
            ]
            if technical_user_name:
                for user in technical_users:
                    if user.username() == technical_user_name:
                        return user
                raise ModelValidationError(
                    f'Did not find technical user "{technical_user_name}" '
                    f'for Github config "{self.name()}"'
                )

            return random.choice(technical_users)

        if self.raw.get('technicalUser'):
            return GithubCredentials(self.raw.get('technicalUser'))

    def matches_hostname(self, host_name):
        return host_name.lower() == urlparse(self.http_url()).hostname.lower()

    def _optional_attributes(self):
        return (
            'httpUrl',
            'purpose_labels',
            'sshUrl',
            'technicalUser',
            'technical_users',
        )

    def _required_attributes(self):
        return [
            'apiUrl',
            'available_protocols',
            'disable_tls_validation',
            'webhook_token',
        ]

    def validate(self):
        super().validate()

        available_protocols = self.available_protocols()
        if len(available_protocols) < 1:
            raise ModelValidationError(
                'At least one available protocol must be configured '
                f"for Github config '{self.name()}'"
            )
        if Protocol.SSH in available_protocols and not self.ssh_url():
            raise ModelValidationError(
                f"SSH url is missing for Github config '{self.name()}'"
            )
        if Protocol.HTTPS in available_protocols and not self.http_url():
            raise ModelValidationError(
                f"HTTP url is missing for Github config '{self.name()}'"
            )

        self.credentials() and self.credentials().validate() # XXX refactor


class GithubCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''

    def auth_token(self):
        tokens = self.raw.get('auth_tokens', None)
        if tokens:
            return random.choice(tokens)
        # fallback to single token
        return self.raw.get('authToken')

    def set_auth_token(self, auth_token):
        self.raw['authToken'] = auth_token

    def private_key(self):
        return self.raw.get('privateKey')

    def email_address(self):
        return self.raw.get('emailAddress')

    def _required_attributes(self):
        required_attribs = set(super()._required_attributes())
        return required_attribs | set(('authToken','privateKey', 'emailAddress'))
