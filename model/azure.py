# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
