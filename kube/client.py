import os

import yaml

import kubernetes.client
from kubernetes.config.kube_config import KubeConfigLoader

from ci.util import (
    ctx as global_ctx,
    existing_file,
    fail,
    not_none,
)

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


class KubernetesClient:
    def __init__(self, kubeconfig_dict:dict = None):
        if not kubeconfig_dict:
            kubeconfig_dict = self._get_kubecfg_from_ctx()
        config = self._config_from_kubeconfig_dict(kubeconfig_dict)
        self.api_client = kubernetes.client.ApiClient(configuration=config)

    def _get_kubecfg_from_ctx(self):
        kubeconfig = os.environ.get('KUBECONFIG', None)
        args = global_ctx().args
        if args and hasattr(args, 'kubeconfig') and args.kubeconfig:
            kubeconfig = args.kubeconfig

        if not kubeconfig:
            fail("Unable to determine kubeconfig from 'KUBECONFIG' env-var or args.")

        kubeconfig = existing_file(kubeconfig)

        with open(kubeconfig, 'r') as kubeconfig_file:
            return yaml.safe_load(kubeconfig_file.read())

    def _config_from_kubeconfig_dict(self, kubeconfig_dict):
        not_none(kubeconfig_dict)
        config = kubernetes.client.Configuration()
        cfg_loader = KubeConfigLoader(dict(kubeconfig_dict))
        cfg_loader.load_and_set(config)
        return config

    def get_cluster_version_info(self):
        return kubernetes.client.VersionApi(self.api_client).get_code()

    def secret_helper(self) -> 'KubernetesSecretHelper':
        return KubernetesSecretHelper(kubernetes.client.CoreV1Api(self.api_client))

    def service_account_helper(self) -> 'KubernetesServiceAccountHelper':
        return KubernetesServiceAccountHelper(kubernetes.client.CoreV1Api(self.api_client))

    def namespace_helper(self) -> 'KubernetesNamespaceHelper':
        return KubernetesNamespaceHelper(kubernetes.client.CoreV1Api(self.api_client))

    def service_helper(self) -> 'KubernetesServiceHelper':
        return KubernetesServiceHelper(kubernetes.client.CoreV1Api(self.api_client))

    def deployment_helper(self) -> 'KubernetesDeploymentHelper':
        return KubernetesDeploymentHelper(kubernetes.client.AppsV1Api(self.api_client))

    def ingress_helper(self) -> 'KubernetesIngressHelper':
        return KubernetesIngressHelper(kubernetes.client.ExtensionsV1beta1Api(self.api_client))

    def pod_helper(self) -> 'KubernetesPodHelper':
        return KubernetesPodHelper(kubernetes.client.CoreV1Api(self.api_client))

    def config_map_helper(self) -> 'KubernetesConfigMapHelper':
        return KubernetesConfigMapHelper(kubernetes.client.CoreV1Api(self.api_client))
