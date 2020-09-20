# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import urllib

from model.base import NamedModelElement
from model.proxy import DockerImageConfig


class ClamAVConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''

    def namespace(self):
        return self.raw.get('namespace')

    def freshclam_image_config(self):
        return DockerImageConfig(self.raw.get('freshclam_image'))

    def clamav_image_config(self):
        return DockerImageConfig(self.raw.get('clamav_image'))

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

    def container_registry_config_name(self):
        return self.raw.get('container_registry')

    def replicas(self):
        return self.raw.get('replicas')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'namespace',
            'service_name',
            'service_port',
            'freshclam_image',
            'clamav_image',
            'replicas',
        ]

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'container_registry',
            'clamd_config_values',
        ]
