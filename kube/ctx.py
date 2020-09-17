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

import os
import typing

import kubernetes.client
from kubernetes import config, client
from kubernetes.config.kube_config import KubeConfigLoader

from ci.util import ctx as global_ctx, fail, existing_file, not_none
import model.kubernetes
from kube.helper import (
    KubernetesConfigMapHelper,
    KubernetesSecretHelper,
    KubernetesServiceAccountHelper,
    KubernetesNamespaceHelper,
    KubernetesServiceHelper,
    KubernetesDeploymentHelper,
    KubernetesIngressHelper,
    KubernetesPodHelper,
)


class Ctx(object):
    '''
    handles the execution context of kubernetes-api calls.
    Most prominently the retrieval of the 'kubeconfig' to use, which is
    either passed via CLI (--kubeconfig) or via env var KUBECONFIG.
    '''

    def __init__(self, kubeconfig_dict: dict=None):
        if not kubeconfig_dict:
            self.kubeconfig = None
            return
        self.set_kubecfg(kubeconfig_dict=kubeconfig_dict)

    def get_kubecfg(self):
        if self.kubeconfig:
            return kubernetes.client.ApiClient(configuration=self.kubeconfig)
        kubeconfig = os.environ.get('KUBECONFIG', None)
        args = global_ctx().args
        if args and hasattr(args, 'kubeconfig') and args.kubeconfig:
            kubeconfig = args.kubeconfig
        if self.kubeconfig:
            kubeconfig = self.kubeconfig
        if not kubeconfig:
            fail('KUBECONFIG env var must be set')
        return config.load_kube_config(existing_file(kubeconfig))

    def set_kubecfg(self, kubeconfig_dict: typing.Union[dict, model.kubernetes.KubernetesConfig]):
        not_none(kubeconfig_dict)
        if isinstance(kubeconfig_dict, model.kubernetes.KubernetesConfig):
            kubeconfig_dict = kubeconfig_dict.kubeconfig()

        configuration = kubernetes.client.Configuration()
        cfg_loader = KubeConfigLoader(dict(kubeconfig_dict))
        cfg_loader.load_and_set(configuration)
        # pylint: disable=no-member
        kubernetes.client.Configuration.set_default(configuration)
        # pylint: enable=no-member
        self.kubeconfig = configuration

    def secret_helper(self) -> 'KubernetesSecretHelper':
        return KubernetesSecretHelper(self.create_core_api())

    def service_account_helper(self) -> 'KubernetesServiceAccountHelper':
        return KubernetesServiceAccountHelper(self.create_core_api())

    def namespace_helper(self) -> 'KubernetesNamespaceHelper':
        return KubernetesNamespaceHelper(self.create_core_api())

    def service_helper(self) -> 'KubernetesServiceHelper':
        return KubernetesServiceHelper(self.create_core_api())

    def deployment_helper(self) -> 'KubernetesDeploymentHelper':
        return KubernetesDeploymentHelper(self.create_apps_api())

    def ingress_helper(self) -> 'KubernetesIngressHelper':
        return KubernetesIngressHelper(self.create_extensions_v1beta1_api())

    def pod_helper(self) -> 'KubernetesPodHelper':
        return KubernetesPodHelper(self.create_core_api())

    def config_map_helper(self) -> 'KubernetesConfigMapHelper':
        return KubernetesConfigMapHelper(self.create_core_api())

    def create_core_api(self):
        cfg = self.get_kubecfg()
        return client.CoreV1Api(cfg)

    def create_rbac_api(self):
        cfg = self.get_kubecfg()
        return client.RbacAuthorizationV1beta1Api(cfg)

    def create_custom_api(self):
        cfg = self.get_kubecfg()
        return client.CustomObjectsApi(cfg)

    def create_apps_api(self):
        cfg = self.get_kubecfg()
        return client.AppsV1Api(cfg)

    def create_extensions_v1beta1_api(self):
        cfg = self.get_kubecfg()
        return client.ExtensionsV1beta1Api(cfg)

    def create_version_api(self):
        cfg = self.get_kubecfg()
        return client.VersionApi(cfg)
