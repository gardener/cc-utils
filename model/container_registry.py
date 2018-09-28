# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

from util import check_type


class ContainerRegistryConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def _optional_attributes(self):
        return {'image_reference_prefixes', 'username', 'password', 'host', 'email'}

    def credentials(self):
        # this cfg currently only contains credentials
        return GcrCredentials(self.raw)

    def image_reference_prefixes(self):
        return self.raw.get('image_reference_prefixes', ())

    def image_ref_matches(self, image_reference: str):
        '''
        returns a boolean indicating whether a given container image reference matches any
        configured image reference prefixes (thus indicating this cfg might be adequate for
        retrieving or deploying the given container image using this cfg).

        If no image reference prefixes are configured, `False` is returned.
        '''
        check_type(image_reference, str)

        prefixes = self.image_reference_prefixes()
        if not prefixes:
            return False

        for prefix in prefixes:
            if image_reference.startswith(prefix):
                return True
        return False


class GcrCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def _optional_attributes(self):
        return {'image_reference_prefixes'}

    def host(self):
        return self.raw.get('host')

    def email(self):
        return self.raw.get('email')
