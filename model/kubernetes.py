# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from model.base import (
    NamedModelElement,
)


class KubernetesConfig(NamedModelElement):
    def _required_attributes(self):
        return {
            'kubeconfig',
        }

    def kubeconfig(self):
        return self.raw.get('kubeconfig')
