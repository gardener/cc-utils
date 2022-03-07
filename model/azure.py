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

from model.base import NamedModelElement


class AzureServicePrincipal(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def tenant_id(self) -> str:
        return self.raw['tenant_id']

    def client_id(self) -> str:
        return self.raw['client_id']

    def client_secret(self) -> str:
        return self.raw['client_secret']

    def object_id(self) -> str:
        return self.raw['object_id']

    def subscription_id(self) -> str:
        return self.raw['subscription_id']

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'client_id',
            'client_secret',
            'subscription_id',
            'tenant_id',
        ]


# XXX export previous name for - temporary - backwards compatibility
ServicePrincipal = AzureServicePrincipal


class AzureStorageAccountCfg(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def storage_account_name(self):
        return self.raw['storage_account_name']

    def access_key(self):
        return self.raw['access_key']

    def container_name(self):
        return self.raw['container_name']

    def container_name_sig(self):
        return self.raw['container_name_sig']

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'storage_account_name',
            'access_key',
            'container_name',
        ]


# XXX export previous name for - temporary - backwards compatibility
StorageAccountConfig = AzureStorageAccountCfg


class AzureSharedGalleryCfg(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def resource_group_name(self):
        return self.raw['resource_group_name']

    def gallery_name(self):
        return self.raw['gallery_name']

    def location(self):
        return self.raw['location']

    def published_name(self):
        return self.raw['published_name']

    def description(self):
        return self.raw['description']

    def eula(self):
        return self.raw['eula']

    def release_note_uri(self):
        return self.raw['release_note_uri']

    def identifier_publisher(self):
        return self.raw['identifier_publisher']

    def identifier_offer(self):
        return self.raw['identifier_offer']

    def identifier_sku(self):
        return self.raw['identifier_sku']

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'resource_group_name',
            'gallery_name',
            'location',
            'published_name',
        ]


# XXX export previous name for - temporary - backwards compatibility
SharedGalleryCfg = AzureSharedGalleryCfg
