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

import ci.util
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
        if (auth_scheme := self.auth_scheme()) is ProtecodeAuthScheme.BASIC_AUTH:
            return BasicCredentials(
                raw_dict={
                    'password': self.raw['credentials']['password'],
                    'username': self.raw['credentials']['username']
                }
            )
        elif auth_scheme is ProtecodeAuthScheme.BEARER_TOKEN:
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

    def group_ids(self) -> list[int]:
        return self.raw.get('group_ids', [])

    def base_url(self) -> str:
        if not (parsed := ci.util.urlparse(self.api_url())):
            return None
        return f'{parsed.scheme}://{parsed.hostname}'

    def matches(
        self,
        group_id: int=None,
        base_url: str=None,
    ) -> int:
        '''
        returns integer in range [-1;2] representing quality of match where -1 states "no match" and
        2 "perfect match".
        '''
        def normalise_url(url: str) -> str:
            parsed = ci.util.urlparse(url)
            return f'{parsed.scheme}://{parsed.hostname}{parsed.path}'

        score = 0

        if base_url:
            base_url = normalise_url(base_url)
            if base_url.startswith(self.base_url()):
                score += 1
            else:
                return -1

        if group_id:
            if group_id in self.group_ids():
                score += 1
            else:
                if self.group_ids():
                    return -1

        return score

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from (
            'api_url',
            'credentials',
        )

    def validate(self):
        # call credentials method to validate credentials
        self.credentials()


def find_config(
    group_id: int | None,
    base_url: str | None,
    config_candidates: typing.Iterable[ProtecodeConfig],
) -> ProtecodeConfig | None:
    matching_cfgs = [
        cfg
        for cfg in config_candidates
        if not cfg.matches(
            group_id=group_id,
            base_url=base_url,
        ) == -1
    ]

    if not matching_cfgs:
        return None

    matching_cfgs = sorted(matching_cfgs, key=lambda c:c.matches(
        group_id=group_id,
        base_url=base_url,
    ))

    # sorted, last element of greatest match quality
    return matching_cfgs[-1]
