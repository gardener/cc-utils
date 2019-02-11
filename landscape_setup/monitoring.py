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

from ensure import ensure_annotations

from landscape_setup import kube_ctx
from landscape_setup.utils import (
    ensure_cluster_version,
    execute_helm_deployment,
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
    monitoring_namespace = kubernetes_cfg.monitoring().namespace()

    # deploy kube-state-metrics
    kube_state_metrics_helm_values = create_kube_state_metrics_helm_values(
        monitoring_cfg=kubernetes_cfg.monitoring()
    )
    execute_helm_deployment(
        kubernetes_cfg,
        monitoring_namespace,
        'stable/kube-state-metrics',
        'kube-state-metrics',
        kube_state_metrics_helm_values,
    )


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
