# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import subprocess
import tempfile
import yaml

from ensure import ensure_annotations

from landscape_setup import kube_ctx
from landscape_setup.utils import (
    ensure_helm_setup,
    ensure_cluster_version,
)
from model.kubernetes import (
    KubernetesConfig,
    MonitoringConfig,
)


@ensure_annotations
def deploy_monitoring_landscape(
    kubernetes_cfg: KubernetesConfig,
):
    # Set the global context to the cluster specified in KubernetesConfig
    kube_ctx.set_kubecfg(kubernetes_cfg.kubeconfig())
    ensure_cluster_version(kubernetes_cfg)

    _deploy_or_upgrade_kube_state_metrics(
        kubernetes_cfg=kubernetes_cfg,
    )


@ensure_annotations
def _deploy_or_upgrade_kube_state_metrics(
        kubernetes_cfg: KubernetesConfig,
):
    """Deploys (or upgrades) kube-state-metrics via Helm Cli"""
    helm_executable = ensure_helm_setup()
    namespace = kubernetes_cfg.monitoring().namespace()

    # create namespace if absent
    namespace_helper = kube_ctx.namespace_helper()
    if not namespace_helper.get_namespace(namespace):
        namespace_helper.create_namespace(namespace)

    KUBE_STATE_METRICS_HELM_VALUES_FILE_NAME = 'kube_state_metrics_helm_values'
    KUBECONFIG_FILE_NAME = 'kubecfg'

    # prepare subprocess args using relative file paths for the values files
    subprocess_args = [
        helm_executable, "upgrade", "--install", "--force",
        "--recreate-pods",
        "--wait",
        "--namespace", namespace,
        # Use Helm's value-rendering mechanism to merge value-sources.
        "--values", KUBE_STATE_METRICS_HELM_VALUES_FILE_NAME,
        namespace, # helm release name is the same as namespace name
        "stable/kube-state-metrics",
    ]

    helm_env = os.environ.copy()

    # create temp dir containing all previously referenced files
    with tempfile.TemporaryDirectory() as temp_dir:
        kubeconfig_path = os.path.join(temp_dir, KUBECONFIG_FILE_NAME)
        helm_env['KUBECONFIG'] = kubeconfig_path
        with open(os.path.join(temp_dir, KUBE_STATE_METRICS_HELM_VALUES_FILE_NAME), 'w') as f:
            yaml.dump(
                create_kube_state_metrics_helm_values(
                    monitoring_cfg=kubernetes_cfg.monitoring(),
                ),
                f
            )
        with open(kubeconfig_path, 'w') as f:
            yaml.dump(kubernetes_cfg.kubeconfig(), f)

        # run helm from inside the temporary directory so that the prepared file paths work
        subprocess.run(subprocess_args, check=True, cwd=temp_dir, env=helm_env)


@ensure_annotations
def create_kube_state_metrics_helm_values(
    monitoring_cfg: MonitoringConfig,
):
    image_tag = monitoring_cfg.kube_state_metrics_version()

    configured_collectors = monitoring_cfg.kube_state_metrics_collectors()
    all_collectors = [
        "configmaps", "cronjobs", "daemonsets", "deployments", "endpoints",
        "horizontalpodautoscalers", "jobs", "limi∏tranges", "namespaces", "nodes",
        "persistentvolumeclaims", "persistentvolumes", "pods", "replicasets",
        "replicationcontrollers", "resourcequotas", "secrets", "services", "statefulsets"
    ]

    def configured(c):
        return c in configured_collectors

    used_collectors = {c: configured(c) for c in all_collectors}

    namespaces_to_monitor = monitoring_cfg.kube_state_metrics_namespaces_to_monitor()

    helm_values = {
        "image": {
            "tag": image_tag,
        },
        "rbac": {
            "create": True
        },
        "podSecurityPolicy": {
            "enabled": True
        },
        "collectors": used_collectors,
        "namespace": ','.join(namespaces_to_monitor)
    }
    return helm_values
