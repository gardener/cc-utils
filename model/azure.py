# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import NamedModelElement


class AzureServicePrincipal(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def tenant_id(self):
        return self.raw['tenant_id']

    def client_id(self):
        return self.raw['client_id']

    def client_secret(self):
        return self.raw['client_secret']

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'tenant_id',
            'client_id',
            'client_secret',
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

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'storage_account_name',
            'access_key',
            'container_name',
        ]


# XXX export previous name for - temporary - backwards compatibility
StorageAccountConfig = AzureStorageAccountCfg
