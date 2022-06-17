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
    ModelBase,
    NamedModelElement,
)
from model.proxy import DockerImageConfig


class SigningServerEndpoint(NamedModelElement):
    def url(self) -> str:
        return self.raw.get('url')


class SigningServerIngressConfig(ModelBase):

    def enabled(self) -> bool:
        return self.raw['enabled']

    def auth(self):
        return self.raw.get('auth')

    def _required_attributes(self):
        return {
            'enabled'
        }


class SigningServerConfig(NamedModelElement):

    def image_config(self) -> DockerImageConfig:
        return DockerImageConfig(self.raw['image'])

    def namespace(self) -> str:
        return self.raw['namespace']

    def log_config(self) -> dict:
        return self.raw['log']

    def replica_count(self) -> int:
        return self.raw['replica_count']

    def image_pull_secret_name(self) -> str:
        return self.raw['image_pull_secret_name']

    def private_key_secret_name(self) -> str:
        return self.raw['private_key_secret_name']

    def private_key(self) -> str:
        return self.raw['private_key']

    def certificate_configmap_name(self) -> str:
        return self.raw['certificate_configmap_name']

    def certificate(self):
        return self.raw['certificate']

    def ca_certificates_configmap_name(self) -> str:
        return self.raw['ca_certificates_configmap_name']

    def ca_certificates(self):
        return self.raw['ca_certificates']

    def max_body_size(self) -> int:
        return self.raw['max_body_size']

    def disable_auth(self) -> bool:
        return self.raw['disable_auth']

    def host(self) -> str:
        return self.raw['host']

    def ingress_config(self) -> SigningServerIngressConfig:
        return SigningServerIngressConfig(self.raw['ingress'])

    def _required_attributes(self):
        return {
            'image',
            'namespace',
            'replica_count',
            'image_pull_secret_name',
            'private_key_secret_name',
            'private_key',
            'certificate_configmap_name',
            'certificate',
            'ca_certificates_configmap_name',
            'ca_certificates',
            'max_body_size',
            'disable_auth',
            'host',
            'ingress',
        }
