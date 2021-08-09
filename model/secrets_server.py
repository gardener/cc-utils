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

import logging

import ci.log
from model.base import (
    NamedModelElement,
)
import model.concourse
import model.secret


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


class SecretsServerConfig(NamedModelElement):
    def _required_attributes(self):
        return {
            'namespace',
            'service_name',
        }

    def _optional_attributes(self):
        return {
            'node_selector',
        }

    def namespace(self):
        return self.raw.get('namespace')

    def service_name(self):
        return self.raw.get('service_name')

    def endpoint_url(self):
        return f'http://{self.service_name()}.{self.namespace()}.svc.cluster.local'

    def node_selector(self):
        return self.raw.get('node_selector')


def secret_url_path(
    job_mapping: model.concourse.JobMapping,
    secret_cfg: model.secret.Secret,
):
    '''
        used to retrieve the secret url path for given config in default template
    '''
    if not secret_cfg:
        logger.warning(f'Secret config not set {secret_cfg=}')

    if job_mapping.secrets_repo():
        return _org_based_secret_url_path(
            target_secret_name=job_mapping.target_secret_name(),
            secret_cfg_name=job_mapping.target_secret_cfg_name(),
        )
    else:
        logger.warning(
            f'No secrets repo for job_mapping {job_mapping.name()} configured. Please do so...',
        )


def _org_based_secret_url_path(target_secret_name, secret_cfg_name):
    return f'{target_secret_name}/{secret_cfg_name}'
