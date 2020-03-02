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


class CheckmarxCredentials(BasicCredentials):
    def client_id(self):
        return self.raw.get('client_id')

    def client_secret(self):
        return self.raw.get('client_secret')

    def domain(self):
        return self.raw.get('domain')

    def scope(self):
        return self.raw.get('scope', 'sast_rest_api')

    def qualified_username(self):
        return f'{self.domain()}\\{self.username()}'


class CheckmarxConfig(NamedModelElement):
    def base_url(self):
        return self.raw.get('base_url')

    def credentials(self):
        return CheckmarxCredentials(self.raw.get('credentials'))

    def _required_attributes(self):
        return 'credentials', 'base_url'
