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


class ElasticSearchConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def endpoints(self):
        return self.raw['endpoints']

    def credentials(self):
        return ElasticSearchCredentials(raw_dict=self.raw['credentials'])

    def _required_attributes(self):
        return ('endpoint_url', 'endpoints')

    def _optional_attributes(self):
        return ('credentials',)


class ElasticSearchCredentials(BasicCredentials):
    pass
