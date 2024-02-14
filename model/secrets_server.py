# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
        logger.warning(f'No secret configuration found, check job_mapping {job_mapping.name()}')
        return

    if job_mapping.secrets_repo():
        if secret_cfg.generation():
            return _org_based_secret_url_path_with_generation(
                target_secret_name=job_mapping.target_secret_name(),
                secret_cfg_name=job_mapping.target_secret_cfg_name(),
                generation=secret_cfg.generation(),
            )
        else:
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


def _org_based_secret_url_path_with_generation(target_secret_name, secret_cfg_name, generation):
    return f'{target_secret_name}-{generation}/{secret_cfg_name}'
