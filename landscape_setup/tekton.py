import tempfile
import yaml

import kube
import model.tekton
import model.kubernetes

from kubernetes import (
    client as kubernetes_client,
    utils as kubernetes_utils,
)

from ci.util import ctx


def deploy_tekton(
    tekton_config: model.tekton.TektonConfig,
):
    cfg_factory = ctx().cfg_factory()

    kubernetes_config_name = tekton_config.kubernetes_config_name()
    kubernetes_cfg = cfg_factory.kubernetes(kubernetes_config_name)

    kube_ctx = kube.ctx.Ctx(kubeconfig_dict=kubernetes_cfg.kubeconfig())
    api_client = kubernetes_client.ApiClient(configuration=kube_ctx.kubeconfig)
    namespace_helper = kube_ctx.namespace_helper()

    if pipelines_config := tekton_config.pipelines_config():
        install_manifests = yaml.safe_load_all(pipelines_config.install_manifests())
        namespace_helper.create_if_absent(pipelines_config.namespace())
        with tempfile.NamedTemporaryFile(mode='w') as temp_file:
            yaml.dump_all(install_manifests, temp_file)
            # This wont work until kubernetes.python v1.12 is released, see
            # https://github.com/kubernetes-client/python/issues/1022
            #
            # For now, a manual install using 'kubectl apply -f' is required.
            kubernetes_utils.create_from_yaml(
                k8s_client=api_client,
                yaml_file=temp_file.name,
                namespace=pipelines_config.namespace(),
            )

    if dashboard_config := tekton_config.dashboard_config():
        install_manifests = yaml.safe_load_all(dashboard_config.install_manifests())
        namespace_helper.create_if_absent(dashboard_config.namespace())
        with tempfile.NamedTemporaryFile(mode='w') as temp_file:
            yaml.dump_all(install_manifests, temp_file)
            # This wont work until kubernetes.python v1.12 is released, see
            # https://github.com/kubernetes-client/python/issues/1022
            #
            # For now, a manual install using 'kubectl apply -f' is required.
            kubernetes_utils.create_from_yaml(
                k8s_client=api_client,
                yaml_file=temp_file.name,
                namespace=dashboard_config.namespace(),
            )
