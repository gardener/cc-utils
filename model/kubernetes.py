# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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


class KubernetesConfig(NamedModelElement):
    def _required_attributes(self):
        return {
            'kubeconfig',
        }

    def kubeconfig(self):
        return self.raw.get('kubeconfig')

    def cluster_version(self):
        return self.raw.get('version')

    def monitoring(self):
        return MonitoringConfig(self.raw.get('monitoring'))


class MonitoringConfig(ModelBase):
    def _required_attributes(self):
        return {
            'namespace',
            'kube_state_metrics_namespaces_to_monitor',
            'kube_state_metrics_collectors',
            'tls_config',
        }

    def namespace(self):
        return self.raw.get('namespace')

    def kube_state_metrics_namespaces_to_monitor(self):
        return self.raw.get('kube_state_metrics_namespaces_to_monitor')

    def kube_state_metrics_collectors(self):
        return self.raw.get('kube_state_metrics_collectors')

    def tls_config(self):
        return self.raw.get('tls_config')
