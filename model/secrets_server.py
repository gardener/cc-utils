# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import (
    NamedModelElement,
    ModelBase,
)


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

    def concourse_attribute(self):
        return self.raw.get('concourse_config').get('attribute')

    def cfg_set_names(self):
        return self.raw['cfg_sets']

    def concourse_cfg_name(self):
        return f'{self.concourse_secret_name()}/{self.concourse_attribute()}'
