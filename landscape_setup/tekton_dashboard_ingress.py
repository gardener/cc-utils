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


@ensure_annotations
def create_tekton_dashboard_helm_values(
    tekton_dashboard_ingress_config: TektonDashboardIngressConfig,
    ingress_config: IngressConfig,
    config_factory,
):
    oauth2_proxy_config = global_ctx().cfg_factory().oauth2_proxy(
        tekton_dashboard_ingress_config.oauth2_proxy_config_name()
    )
    helm_values = {
        'external_url': tekton_dashboard_ingress_config.external_url(),
        'ingress_host': tekton_dashboard_ingress_config.ingress_host(config_factory),
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
    tekton_dashboard_ingress_config: TektonDashboardIngressConfig,
    chart_dir: str,
    deployment_name: str,
):
    not_empty(deployment_name)

    cfg_factory = global_ctx().cfg_factory()
    chart_dir = os.path.abspath(chart_dir)

    kubernetes_config = cfg_factory.kubernetes(
        tekton_dashboard_ingress_config.kubernetes_config_name()
    )
    kube_ctx.set_kubecfg(kubernetes_config.kubeconfig())

    ingress_config = cfg_factory.ingress(tekton_dashboard_ingress_config.ingress_config())
    helm_values = create_tekton_dashboard_helm_values(
        tekton_dashboard_ingress_config=tekton_dashboard_ingress_config,
        ingress_config=ingress_config,
        config_factory=cfg_factory,
    )

    execute_helm_deployment(
        kubernetes_config,
        tekton_dashboard_ingress_config.namespace(),
        chart_dir,
        deployment_name,
        helm_values,
    )
