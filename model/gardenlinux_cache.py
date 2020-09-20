# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import urllib

from model.base import NamedModelElement


class GardenlinuxCacheConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''

    def namespace(self):
        return self.raw.get('namespace')

    def external_url(self):
        return self.raw.get('external_url')

    def image_reference(self):
        return self.raw.get('image_reference')

    def image_tag(self):
        return self.raw.get('image_tag')

    def ingress_config(self):
        return self.raw.get('ingress_config')

    def ingress_host(self):
        return self.raw.get('ingress_host')

    def volume_size(self) -> str:
        return self.raw.get('volume_size')

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

    def replicas(self):
        return self.raw.get('replicas')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'external_url',
            'image_reference',
            'image_tag',
            'ingress_config',
            'ingress_host',
            'namespace',
            'replicas',
            'service_name',
            'service_port',
            'volume_size',
        ]
