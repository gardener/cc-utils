# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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

    def cosign_repository(self) -> str:
        return self.raw['cosign_repository']

    def ingress_config(self) -> SigningServerIngressConfig:
        return SigningServerIngressConfig(self.raw['ingress'])
