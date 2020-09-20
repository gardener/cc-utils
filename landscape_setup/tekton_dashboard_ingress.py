# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os

from ensure import ensure_annotations

from ci.util import (
    ctx as global_ctx,
    not_empty,
)
from landscape_setup import kube_ctx
from landscape_setup.utils import (
    execute_helm_deployment,
)
from model.tekton_dashboard_ingress import TektonDashboardIngressConfig
from model.ingress import IngressConfig
from model.kubernetes import KubernetesConfig


@ensure_annotations
def create_tekton_dashboard_helm_values(
    tekton_dashboard_ingress_config: TektonDashboardIngressConfig,
    ingress_config: IngressConfig,
):
    oauth2_proxy_config = global_ctx().cfg_factory().oauth2_proxy(
        tekton_dashboard_ingress_config.oauth2_proxy_config_name()
    )
    helm_values = {
        'external_url': tekton_dashboard_ingress_config.external_url(),
        'ingress_host': tekton_dashboard_ingress_config.ingress_host(),
        'ingress_issuer_name': ingress_config.issuer_name(),
        'ingress_tls_hosts': ingress_config.tls_host_names(),
        'ingress_ttl': str(ingress_config.ttl()),
        'serviceName': tekton_dashboard_ingress_config.service_name(),
        'servicePort': tekton_dashboard_ingress_config.service_port(),
        'oauthProxyAuthUrl': oauth2_proxy_config.external_url(),
    }
    return helm_values


@ensure_annotations
def deploy_tekton_dashboard_ingress(
    kubernetes_config: KubernetesConfig,
    tekton_dashboard_ingress_config: TektonDashboardIngressConfig,
    chart_dir: str,
    deployment_name: str,
):
    not_empty(deployment_name)

    cfg_factory = global_ctx().cfg_factory()
    chart_dir = os.path.abspath(chart_dir)

    kube_ctx.set_kubecfg(kubernetes_config.kubeconfig())

    ingress_config = cfg_factory.ingress(tekton_dashboard_ingress_config.ingress_config())
    helm_values = create_tekton_dashboard_helm_values(
        tekton_dashboard_ingress_config=tekton_dashboard_ingress_config,
        ingress_config=ingress_config,
    )

    execute_helm_deployment(
        kubernetes_config,
        tekton_dashboard_ingress_config.namespace(),
        chart_dir,
        deployment_name,
        helm_values,
    )
