# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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
    LiteralStr,
)
from model import (
    ConfigFactory,
    ConfigurationSet,
)
from model.monitoring import (
    CCMonitoringConfig,
)
from model.concourse import (
    ConcourseConfig,
)


@ensure_annotations
def deploy_monitoring_landscape(
    cfg_set: ConfigurationSet,
    cfg_factory: ConfigFactory,
):
    kubernetes_cfg = cfg_set.kubernetes()
    concourse_cfg = cfg_set.concourse()

    # Set the global context to the cluster specified in KubernetesConfig
    kube_ctx.set_kubecfg(kubernetes_cfg.kubeconfig())
    ensure_cluster_version(kubernetes_cfg)

    monitoring_config_name = concourse_cfg.monitoring_config()
    monitoring_cfg = cfg_factory.monitoring(monitoring_config_name)
    monitoring_namespace = monitoring_cfg.namespace()

    # deploy kube-state-metrics
    kube_state_metrics_helm_values = create_kube_state_metrics_helm_values(
        monitoring_cfg=monitoring_cfg
    )
    execute_helm_deployment(
        kubernetes_cfg,
        monitoring_namespace,
        'stable/kube-state-metrics',
        'kube-state-metrics',
        kube_state_metrics_helm_values,
    )

    # deploy postgresql exporter
    postgresql_helm_values = create_postgresql_helm_values(
        concourse_cfg=concourse_cfg,
        cfg_factory=cfg_factory,
    )
    execute_helm_deployment(
        kubernetes_cfg,
        monitoring_namespace,
        'stable/prometheus-postgres-exporter',
        'prometheus-postgres-exporter',
        postgresql_helm_values,
    )


@ensure_annotations
def create_kube_state_metrics_helm_values(
    monitoring_cfg: CCMonitoringConfig,
):
    configured_collectors = monitoring_cfg.kube_state_metrics().collectors()
    all_collectors = [
        "configmaps", "cronjobs", "daemonsets", "deployments", "endpoints",
        "horizontalpodautoscalers", "jobs", "limitranges", "namespaces", "nodes",
        "persistentvolumeclaims", "persistentvolumes", "poddisruptionbudgets", "pods",
        "replicasets", "replicationcontrollers", "resourcequotas", "secrets",
        "services", "statefulsets"
    ]

    def configured(c):
        return c in configured_collectors

    used_collectors = {c: configured(c) for c in all_collectors}

    namespaces_to_monitor = monitoring_cfg.kube_state_metrics().namespaces_to_monitor()

    helm_values = {
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


def create_postgresql_helm_values(
    concourse_cfg: ConcourseConfig,
    cfg_factory: ConfigFactory,
):
    helm_chart_default_values_name = concourse_cfg.helm_chart_default_values_config()
    default_helm_values = cfg_factory.concourse_helmchart(helm_chart_default_values_name).raw
    helm_chart_values_name = concourse_cfg.helm_chart_values()
    custom_helm_values = cfg_factory.concourse_helmchart(helm_chart_values_name).raw

    helm_values = {
        "service": {
            "annotations": {
                "prometheus.io/scrape": "true"
            }
        },
        "rbac": {
            "create": False,
            "pspEnabled": False
        },
        "serviceAccount": {
            "create": False
        },
        "config": {
            "datasource": {
                "host": "concourse-postgresql.concourse.svc.cluster.local",
                "user": default_helm_values.get('postgresql').get('postgresUser'),
                "password": custom_helm_values.get('postgresql').get('postgresPassword'),
                "database": default_helm_values.get('postgresql').get('postgresDatabase'),
                "sslmode": "disable"
            },
            "disableDefaultMetrics": True,
            "queries": LiteralStr('''
                pg_database:
                    query: "SELECT pg_database.datname, pg_database_size(pg_database.datname)
                            as size FROM pg_database"
                    metrics:
                    - datname:
                        usage: "LABEL"
                        description: "Name of the database"
                    - size:
                        usage: "GAUGE"
                        description: "Disk space used by the database" '''
            )
        }
    }
    return helm_values
