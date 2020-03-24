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

from model.base import (
    NamedModelElement,
    ModelBase,
)


class CCMonitoringConfig(NamedModelElement):
    def _required_attributes(self):
        return {
            'basic_auth_pwd',
            'basic_auth_secret_name',
            'basic_auth_user',
            'external_url',
            'ingress_host',
            'kube_state_metrics',
            'namespace',
            'postgresql_exporter',
            'tls_secret_name',
        }

    def namespace(self):
        return self.raw.get('namespace')

    def kube_state_metrics(self):
        return KubeStateMetrics(raw_dict=self.raw['kube_state_metrics'])

    def postgresql_exporter(self):
        return PostgresqlExporter(raw_dict=self.raw['postgresql_exporter'])

    def tls_secret_name(self):
        return self.raw.get('tls_secret_name')

    def basic_auth_secret_name(self):
        return self.raw.get('basic_auth_secret_name')

    def ingress_host(self):
        return self.raw.get('ingress_host')

    def external_url(self):
        return self.raw.get('external_url')

    def basic_auth_user(self):
        return self.raw.get('basic_auth_user')

    def basic_auth_pwd(self):
        return self.raw.get('basic_auth_pwd')


class KubeStateMetrics(ModelBase):
    def namespaces_to_monitor(self):
        return self.raw.get('namespaces_to_monitor')

    def collectors(self):
        return self.raw.get('collectors')

    def service_name(self):
        return self.raw.get('service_name')

    def service_port(self):
        return self.raw.get('service_port')


class PostgresqlExporter(ModelBase):
    def service_name(self):
        return self.raw.get('service_name')

    def service_port(self):
        return self.raw.get('service_port')
