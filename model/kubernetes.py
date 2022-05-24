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
import typing

from model.base import (
    ModelBase,
    NamedModelElement,
)


class ServiceAccountConfig(ModelBase):
    def _required_attributes(self):
        return {
            'name',
            'namespace',
        }

    def name(self) -> str:
        return self.raw['name']

    def namespace(self) -> str:
        return self.raw['namespace']


class KubernetesConfig(NamedModelElement):
    def _required_attributes(self):
        return {
            'kubeconfig',
        }

    def service_account(self) -> typing.Union[ServiceAccountConfig, None]:
        if raw_cfg := self.raw.get('service_account'):
            return ServiceAccountConfig(raw_dict=raw_cfg)
        else:
            return None

    def kubeconfig(self):
        return self.raw.get('kubeconfig')

    def cluster_domain(self):
        return self.raw.get('cluster_domain')

    def namespace(self):
        '''
        fallback to default if no namespace is configured
        '''
        return self.raw.get('namespace', 'default')
