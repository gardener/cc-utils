# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class JiraConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def _required_attributes(self):
        return ['base_url', 'credentials']

    def credentials(self):
        return JiraCredentials(self.raw['credentials'])

    def base_url(self):
        return self.raw['base_url']


class JiraCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def email(self):
        return self.raw['email']

    def full_name(self):
        return self.raw['full_name']
