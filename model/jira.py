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
