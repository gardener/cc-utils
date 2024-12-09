# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import model.base


class DeliveryEndpointsCfg(model.base.NamedModelElement):
    def service_host(self):
        return self.raw['service_host']

    def base_url(self):
        return f'http://{self.service_host()}'

    def dashboard_host(self):
        return self.raw['dashboard_host']

    def dashboard_url(self):
        return f'https://{self.dashboard_host()}'
