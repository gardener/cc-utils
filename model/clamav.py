# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

from model.base import NamedModelElement
from model.proxy import DockerImageConfig


class ClamAVConfig(NamedModelElement):
    '''Not intended to be instantiated by users of this module
    '''

    def namespace(self):
        return self.raw.get('namespace')

    def freshclam_image_config(self):
        return DockerImageConfig(self.raw.get('freshclam_image'))

    def clamav_image_config(self):
        return DockerImageConfig(self.raw.get('clamav_image'))

    def service_name(self):
        return self.raw.get('service_name')

    def service_port(self):
        return self.raw.get('service_port')

    def cluster_url(self):
        return f'{self.service_name}.{self.namespace()}.svc.cluster.local:{self.service_port()}'

    def container_registry_config_name(self):
        return self.raw.get('container_registry')

    def replicas(self):
        return self.raw.get('replicas')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'namespace',
            'service_name',
            'service_port',
            'freshclam_image',
            'clamav_image',
            'replicas',
        ]

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'container_registry',
        ]
