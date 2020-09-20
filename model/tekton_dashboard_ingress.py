# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import NamedModelElement


class TektonDashboardIngressConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''

    def namespace(self):
        return self.raw.get('namespace')

    def external_url(self):
        return self.raw.get('external_url')

    def ingress_config(self):
        return self.raw.get('ingress_config')

    def ingress_host(self):
        return self.raw.get('ingress_host')

    def service_name(self):
        return self.raw.get('service_name')

    def service_port(self):
        return self.raw.get('service_port')

    def oauth2_proxy_config_name(self):
        return self.raw.get('oauth2_proxy_config_name')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'external_url',
            'ingress_config',
            'ingress_host',
            'namespace',
            'service_name',
            'service_port',
        ]
