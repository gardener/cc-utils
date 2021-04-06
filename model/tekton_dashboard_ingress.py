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

from . import cluster_domain_from_kubernetes_config
from model.base import NamedModelElement


TEKTON_INGRESS_SUBDOMAIN_LABEL = 'tekton-dashboard'


class TektonDashboardIngressConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''

    def namespace(self):
        return self.raw.get('namespace')

    def external_url(self, cfg_factory):
        return self.ingress_host(cfg_factory)

    def ingress_config(self):
        return self.raw.get('ingress_config')

    def ingress_host(self, cfg_factory):
        cluster_domain = cluster_domain_from_kubernetes_config(
            cfg_factory,
            self.kubernetes_config_name(),
        )
        return f'{self.subdomain_label()}.{cluster_domain}'

    def subdomain_label(self):
        return self.raw.get('subdomain_label', TEKTON_INGRESS_SUBDOMAIN_LABEL)

    def kubernetes_config_name(self):
        return self.raw.get('kubernetes_config')

    def service_name(self):
        return self.raw.get('service_name')

    def service_port(self):
        return self.raw.get('service_port')

    def oauth2_proxy_config_name(self):
        return self.raw.get('oauth2_proxy_config_name')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'ingress_config',
            'kubernetes_config',
            'namespace',
            'service_name',
            'service_port',
        ]

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'subdomain_label',
        ]
