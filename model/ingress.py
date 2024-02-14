# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import (
    NamedModelElement,
)


class IngressConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def tls_host_names(self):
        return self.raw['tls_host_names']

    def ttl(self):
        return self.raw['ttl']

    def issuer_name(self):
        return self.raw['issuer_name']

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'issuer_name',
            'tls_host_names',
            'ttl',
        ]
