# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import typing

import ci.util
from model.base import (
    NamedModelElement,
    TokenCredentials,
)


class BDBAConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def credentials(self) -> TokenCredentials:
        return TokenCredentials(
            raw_dict={
                'token': self.raw['credentials']['token'],
            }
        )

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
    config_candidates: typing.Iterable[BDBAConfig],
) -> BDBAConfig | None:
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
