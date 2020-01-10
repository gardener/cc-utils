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
    ModelBase,
    NamedModelElement,
)


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


class MitmLoggingConfig(ModelBase):
    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'els_config',
            'els_index',
        ]

    def els_config_name(self):
        return self.raw['els_config']

    def els_index_name(self):
        return self.raw['els_index']


class ProxyConfig(NamedModelElement):
    '''Encompasses all configuration necessary for the deployment of a MitM-Proxy alongside
    Concourse.

    Consists of two sub-configs: the config for the init container required to setup our Pod for the
    mitm-proxy and the configuration of the MitM-proxy.
    '''
    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'mitm_proxy',
            'sidecar_image',
        ]

    def mitm_proxy(self):
        return MitmProxyConfig(raw_dict=self.raw['mitm_proxy'])

    def sidecar_image(self):
        return DockerImageConfig(raw_dict=self.raw['sidecar_image'])


class MitmProxyConfig(DockerImageConfig):
    '''A combination of docker image reference and MitM-Config.

    The content of the config attribute is a mapping of MitM-Proxy options to their values. An
    annotated example of all options can be obtained by running 'mitmproxy --options'.
    '''
    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'config',
            'logging',
        ]

    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'filter_config',
        ]

    def config(self):
        return self.raw['config']

    def logging(self):
        return MitmLoggingConfig(self.raw['logging'])

    def filter_config(self):
        config = self.raw.get('filter_config')
        if not config:
            return None
        return MitmFilterConfig(config)


class MitmFilterConfig(ModelBase):
    def _optional_attributes(self):
        yield from super()._optional_attributes()
        yield from [
            'whitelisted_hosts',
            'blacklisted_hosts',
        ]

    def whitelisted_hosts(self):
        return self.raw.get('whitelisted_hosts', ())

    def blacklisted_hosts(self):
        return self.raw.get('blacklisted_hosts', ())
