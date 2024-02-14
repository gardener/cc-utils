# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import urllib

from model.base import NamedModelElement


class FreshclamConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''
    def service_name(self):
        return self.raw.get('service_name')

    def service_port(self):
        return self.raw.get('service_port')

    def replicas(self):
        return self.raw.get('replicas')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'replicas',
            'service_name',
            'service_port',
        ]


class ClamAVConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''

    def namespace(self):
        return self.raw.get('namespace')

    def freshclam_config(self) -> FreshclamConfig | None:
        if raw := self.raw.get('freshclam'):
            return FreshclamConfig(name='freshclam_config', raw_dict=raw)

    def service_name(self):
        return self.raw.get('service_name')

    def service_port(self):
        return self.raw.get('service_port')

    def service_url(self):
        return urllib.parse.urlunparse((
            'http',
            f'{self.service_name()}.{self.namespace()}.svc.cluster.local:{self.service_port()}',
            '',
            '',
            '',
            '',
        ))

    def clamd_config_values(self):
        return self.raw.get('clamd_config_values', {})

    def replicas(self):
        return self.raw.get('replicas')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'namespace',
            'replicas',
            'service_name',
            'service_port',
        ]

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'clamd_config_values',
            'freshclam',
        ]
