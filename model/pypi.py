# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class PyPiCfg(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def credentials(self):
        return PyPiCredentials(self.raw.get('credentials'))


class PyPiCredentials(BasicCredentials):
    pass
