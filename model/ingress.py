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
