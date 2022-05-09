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

import enum
import typing

from model.base import (
    ModelBase,
    NamedModelElement,
)


class DeliveryConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def auth(self):
        return Auth(self.raw.get('auth'))

    def service(self):
        return DeliverySvcCfg(self.raw.get('service'))

    def dashboard(self):
        return DeliveryDashboardCfg(self.raw.get('dashboard'))

    def db_cfg_name(self):
        return self.raw.get('db_cfg_name')

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'db_cfg_name',
        ]

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'service',
        ]


class OAuthType(enum.Enum):
    GITHUB = 'github'


class Auth(ModelBase):
    def oauth_cfgs(self):
        return [OAuth(raw) for raw in self.raw.get('oauth_cfgs')]

    def oauth_cfg(self, github_cfg):
        for oc in self.oauth_cfgs():
            if oc.github_cfg() == github_cfg:
                return oc
        raise KeyError(f'no oauth cfg for {github_cfg}')


class OAuth(ModelBase):
    def name(self) -> str:
        return self.raw.get('name')

    def type(self):
        return OAuthType(self.raw.get('type'))

    def github_cfg(self):
        return self.raw.get('github_cfg')

    def oauth_url(self):
        return self.raw.get('oauth_url')

    def token_url(self):
        return self.raw.get('token_url')

    def client_id(self):
        return self.raw.get('client_id')

    def client_secret(self):
        return self.raw.get('client_secret')

    def scope(self):
        return self.raw.get('scope')


class SigningCfg(ModelBase):
    def id(self) -> str:
        return self.raw.get('id')

    def algorithm(self):
        return self.raw.get('algorithm', 'HS256')

    def secret(self):
        return self.raw.get('secret')

    def purpose_labels(self) -> list[str]:
        return self.raw['purpose_labels']


class DeliveryDashboardCfg(ModelBase):
    def deployment_name(self):
        return self.raw.get('deployment_name', 'delivery-dashboard')


class DeliverySvcCfg(ModelBase):
    def deployment_name(self):
        return self.raw.get('deployment_name', 'delivery-service')

    def signing_cfgs(
        self,
        purpose_label: str = None,
    ) -> typing.Union[SigningCfg, list[SigningCfg], None]:
        cfgs = self.raw.get('signing')
        if purpose_label:
            for raw_singing_Cfg in cfgs:
                signing_cfg = SigningCfg(raw_singing_Cfg)
                if not (labels := signing_cfg.purpose_labels()):
                    return None

                if purpose_label in labels:
                    return signing_cfg

        return [SigningCfg(raw) for raw in cfgs]


class DeliveryEndpointsCfg(NamedModelElement):
    def service_host(self):
        return self.raw['service_host']

    def base_url(self):
        return f'http://{self.service_host()}'

    def dashboard_host(self):
        return self.raw['dashboard_host']

    def dashboard_url(self):
        return f'https://{self.dashboard_host()}'
