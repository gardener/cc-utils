# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import urllib

from . import cluster_domain_from_kubernetes_config
from model.base import NamedModelElement


GARDENLINUX_CACHE_SUBDOMAIN_LABEL = 'snapshot-cache'


class GardenlinuxCacheConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''

    def namespace(self):
        return self.raw.get('namespace')

    def image_reference(self):
        return self.raw.get('image_reference')

    def external_url(self, cfg_factory):
        return self.ingress_host(cfg_factory)

    def image_tag(self):
        return self.raw.get('image_tag')

    def ingress_config(self):
        return self.raw.get('ingress_config')

    def ingress_host(self, cfg_factory):
        cluster_domain = cluster_domain_from_kubernetes_config(
            cfg_factory,
            self.kubernetes_config_name(),
        )
        return f'{self.subdomain_label()}.{cluster_domain}'

    def subdomain_label(self):
        return self.raw.get('subdomain_label', GARDENLINUX_CACHE_SUBDOMAIN_LABEL)

    def kubernetes_config_name(self):
        return self.raw.get('kubernetes_config')

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
            'image_reference',
            'image_tag',
            'ingress_config',
            'kubernetes_config',
            'namespace',
            'replicas',
            'service_name',
            'service_port',
            'volume_size',
        ]

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'subdomain_label',
        ]
