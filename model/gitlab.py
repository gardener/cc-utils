# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    BasicCredentials,
    NamedModelElement,
)


class GitlabConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def purpose_labels(self):
        return set(self.raw.get('purpose_labels', ()))

    def ssh_url(self):
        return self.raw.get('sshUrl')

    def http_url(self):
        return self.raw.get('httpUrl')

    def credentials(self):
        return GitlabCredentials(self.raw.get('technicalUser'))

    def _optional_attributes(self):
        return (
            'purpose_labels',
        )

    def _required_attributes(self):
        return [
            'sshUrl',
            'httpUrl',
            'technicalUser'
        ]

    def validate(self):
        super().validate()
        # validation of credentials implicitly happens in the constructor
        self.credentials()


class GitlabCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''

    def email_address(self):
        return self.raw.get('emailAddress')

    def private_key(self):
        return self.raw.get('privateKey')

    def access_token(self):
        return self.raw.get('accessToken')

    def _required_attributes(self):
        required_attribs = set(super()._required_attributes())
        return required_attribs | set(('privateKey', 'emailAddress', 'accessToken'))
