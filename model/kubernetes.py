# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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
)


class KubernetesConfig(NamedModelElement):
    def kubeconfig(self):
        return self.raw.get('kubeconfig')

    def cluster_version(self):
        return self.raw.get('version')

    def ingress_host(self, ingress_host_prefix:str = None):
        ingress_host = self.raw.get('ingress_host')
        if ingress_host_prefix:
            return "{ingress_host_prefix}.{ingress_host}".format(
                ingress_host_prefix=ingress_host_prefix,
                ingress_host=ingress_host,)

        return ingress_host

    def ingress_url(self, ingress_host_prefix:str = None):
        return 'https://' + self.ingress_host(ingress_host_prefix)
