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
import urllib

from . import cluster_domain_from_kubernetes_config
from model.base import NamedModelElement


GARDENLINUX_CACHE_SUBDOMAIN_LABEL = 'snapshot-cache'


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

    def ingress_host(self, cfg_factory):
        cluster_domain = cluster_domain_from_kubernetes_config(
            cfg_factory,
            self.kubernetes_cluster_config(),
        )
        return f'{GARDENLINUX_CACHE_SUBDOMAIN_LABEL}.{cluster_domain}'

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
            'external_url',
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
