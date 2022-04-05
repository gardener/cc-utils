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
import deprecated

from . import cluster_domain_from_kubernetes_config
from model.base import (
    NamedModelElement,
    ModelDefaultsMixin,
)
from model.proxy import DockerImageConfig


class WebhookDispatcherConfig(NamedModelElement, ModelDefaultsMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_defaults(self.raw)

    def _required_attributes(self):
        return {
            'concourse_config_names',
        }

    def _defaults_dict(self):
        return {
            'pipeline_templates_path': ['/cc/utils/concourse/templates'],
            'pipeline_include_path': '/cc/utils/concourse',
        }

    def pipeline_templates_path(self):
        return self.raw['pipeline_templates_path']

    def pipeline_include_path(self):
        return self.raw['pipeline_include_path']

    def concourse_config_names(self):
        return self.raw['concourse_config_names']


WHD_DEPLOYMENT_SUBDOMAIN_LABEL = 'webhooks'


class WebhookDispatcherDeploymentConfig(NamedModelElement):
    def _required_attributes(self):
        return {
            'whd_image',
            'ingress_config',
            'external_url',
            'secrets_server_config',
            'kubernetes_config',
            'webhook_dispatcher_config',
            'container_port',
            'logging_els_index',
            'job_mapping_name',
        }

    @deprecated.deprecated
    def image_reference(self):
        image_config = self.image_config()
        return image_config.image_reference()

    def external_url(self):
        return self.raw.get('external_url')

    def ingress_config(self):
        return self.raw.get('ingress_config')

    def secrets_server_config_name(self):
        return self.raw.get('secrets_server_config')

    def job_mapping_name(self) -> str:
        return self.raw.get('job_mapping_name')

    def ingress_host(self, cfg_factory):
        cluster_domain = cluster_domain_from_kubernetes_config(
            cfg_factory,
            self.kubernetes_config_name(),
        )
        return f'{WHD_DEPLOYMENT_SUBDOMAIN_LABEL}.{cluster_domain}'

    def events(self):
        return self.raw.get('events', ['*'])

    def kubernetes_config_name(self):
        return self.raw.get('kubernetes_config')

    def webhook_dispatcher_config_name(self):
        return self.raw.get('webhook_dispatcher_config')

    def webhook_dispatcher_container_port(self):
        return self.raw['container_port']

    def image_config(self):
        return DockerImageConfig(self.raw.get('whd_image'))

    def logging_els_index(self):
        '''Name of the elastic-search index to log into'''
        return self.raw['logging_els_index']
