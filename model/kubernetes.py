# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum
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


class RotationStrategy(enum.StrEnum):
    SECRET = 'secret'
    TOKEN_REQUEST = 'tokenRequest'


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

    def kubeconfig(self) -> dict:
        return self.raw.get('kubeconfig')

    def cluster_domain(self):
        return self.raw.get('cluster_domain')

    def namespace(self):
        '''
        fallback to default if no namespace is configured
        '''
        return self.raw.get('namespace', 'default')

    def rotation_strategy(self) -> RotationStrategy:
        raw = self.raw.get('rotation_strategy', 'secret')
        return RotationStrategy(raw)
