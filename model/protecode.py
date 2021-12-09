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

from model.base import (
    BasicCredentials,
    NamedModelElement,
    TokenCredentials,
)


class ProtecodeAuthScheme(enum.Enum):
    BASIC_AUTH = 'basic_auth'
    BEARER_TOKEN = 'bearer_token'


class ProtecodeConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def auth_scheme(self) -> ProtecodeAuthScheme:
        return ProtecodeAuthScheme(self.raw['credentials'].get('auth_scheme', 'basic_auth'))

    def credentials(self) -> typing.Union[BasicCredentials, TokenCredentials]:
        if (auth_scheme := self.auth_scheme()) is ProtecodeAuthScheme.BEARER_TOKEN:
            return TokenCredentials(
                raw_dict={
                    'token': self.raw['credentials']['token'],
                }
            )
        else:
            raise NotImplementedError(auth_scheme)

    def api_url(self) -> str:
        return self.raw.get('api_url')

    def tls_verify(self) -> bool:
        return self.raw.get('tls_verify', True)

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from (
            'api_url',
            'credentials',
        )

    def validate(self):
        # call credentials method to validate credentials
        self.credentials()
