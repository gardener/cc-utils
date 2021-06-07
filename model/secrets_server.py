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
    ModelBase,
)
import model.concourse
import model.secret
import typing


class SecretsServerConfig(NamedModelElement):
    def _required_attributes(self):
        return {
            'namespace',
            'service_name',
            'secrets',
        }

    def namespace(self):
        return self.raw.get('namespace')

    def service_name(self):
        return self.raw.get('service_name')

    def endpoint_url(self):
        return 'http://{sn}.{ns}.svc.cluster.local'.format(
            sn=self.service_name(),
            ns=self.namespace(),
        )

    def kubernetes_cluster_config(self):
        return self.raw.get('kubernetes_cluster_config')

    def secrets(self):
        return SecretsServerSecrets(raw_dict=self.raw['secrets'])


class SecretsServerSecrets(ModelBase):
    def _required_attributes(self):
        return {
            'concourse_config',
            'cfg_sets',
        }

    def concourse_secret_name(self):
        return self.raw.get('concourse_config').get('name')

    def encrypted_concourse_secret_name(self):
        return self.raw.get('encrypted_concourse_config').get('name')

    def concourse_attribute(self):
        return self.raw.get('concourse_config').get('attribute')

    def encrypted_concourse_attribute(self):
        return self.raw.get('encrypted_concourse_config').get('attribute')

    def cfg_set_names(self):
        return self.raw['cfg_sets']

    def concourse_cfg_name(self):
        return f'{self.concourse_secret_name()}/{self.concourse_attribute()}'

    def encrypted_concourse_cfg_name(self):
        return f'{self.encrypted_concourse_secret_name()}/{self.encrypted_concourse_attribute()}'

    def secret_url_path(
        self,
        job_mapping: model.concourse.JobMapping,
        secret_cfg: typing.Optional[model.secret.Secret],
    ):
        '''
            used to retrieve the secret url path for given config in default template
        '''
        if secret_cfg:
            if job_mapping.secrets_repo():
                # FIXME enable org based secret path as soon as org secret repos are commonly used
                return self.encrypted_concourse_cfg_name()
                # return f'{job_mapping.target_secret_name()}/{job_mapping.team_name()}_cfg'
            else:
                return self.encrypted_concourse_cfg_name()
        else:
            return self.concourse_cfg_name()
