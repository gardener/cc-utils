# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class ProtecodeConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def credentials(self):
        return ProtecodeCredentials(self.raw.get('credentials'))

    def api_url(self):
        return self.raw.get('api_url')

    def tls_verify(self):
        return self.raw.get('tls_verify', True)


class ProtecodeCredentials(BasicCredentials):
    pass
