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
