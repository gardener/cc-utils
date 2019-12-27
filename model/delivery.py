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
    BasicCredentials,
    ModelBase,
    NamedModelElement,
)


class DeliveryConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def tls_cfg_name(self):
        return self.raw.get('tls_cfg_name')

    def dashboard(self):
        return DeliveryDashboardCfg(self.raw.get('dashboard'))

    def service(self):
        return DeliverySvcCfg(self.raw.get('service'))

    def deployment_name(self):
        return self.raw.get('deployment_name', 'delivery-dashboard')

    def mongodb_config(self):
        if not self.raw.get('mongodb'):
            return None
        return MongoDbConfig(self.raw.get('mongodb'))

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'mongodb',
            'deployment_name',
        ]

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'tls_cfg_name',
            'dashboard',
            'service',
        ]


class DeliverySvcCfg(ModelBase):
    def external_host(self):
        return self.raw.get('external_host')


class DeliveryDashboardCfg(ModelBase):
    def external_host(self):
        return self.raw.get('external_host')


class MongoDbConfig(ModelBase):
    '''
    Not intended to be instantiated by users of this module
    '''

    def credentials(self):
        return BasicCredentials(self.raw.get('credentials'))

    def configmap(self):
        '''Entries for the MongoDB config file.
        '''
        return self.raw.get('configmap')

    def database_name(self):
        return self.raw.get('database_name', 'delivery')

    def service_port(self):
        '''Return the port on which the kubernetes cluster-service is listening.
        '''
        return self.raw.get('service_port', 27017)

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'configmap',
            'service_port',
            'database_name',
        ]

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'credentials',
        ]
