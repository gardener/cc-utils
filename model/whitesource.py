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
    ModelBase,
)


class WhitesourceCredentials(BasicCredentials):
    def user(self):
        return self.raw.get('user')

    def user_key(self):
        return self.raw.get('user_key')


class WhitesourceConfig(NamedModelElement):
    def wss_endpoint(self):
        return self.raw.get('wss_endpoint')

    def wss_api_endpoint(self):
        return self.raw.get('wss_api_endpoint')

    def api_key(self):
        return self.raw.get('api_key')

    def namespace(self):
        return self.raw.get('namespace')

    def extension_endpoint(self):
        return self.raw.get('extension_endpoint')

    def credentials(self):
        return WhitesourceCredentials(self.raw.get('credentials'))

    def _defaults_dict(self):
        return {
            'namespace': 'whitesource-api-extension',
        }

    def _required_attributes(self):
        return 'credentials', 'wss_endpoint', 'api_key', 'extension_endpoint'


class DockerImageConfig(ModelBase):
    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'image_name',
            'image_tag',
        ]

    def image_name(self):
        return self.raw['image_name']

    def image_tag(self):
        return self.raw['image_tag']

    def image_reference(self):
        return f'{self.image_name()}:{self.image_tag()}'
