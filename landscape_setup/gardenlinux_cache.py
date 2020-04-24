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
    ensure_cluster_version,
    execute_helm_deployment,
)
from model.gardenlinux_cache import GardenlinuxCacheConfig
from model.ingress import IngressConfig
from model.kubernetes import KubernetesConfig


@ensure_annotations
def create_gardenlinux_cache_helm_values(
    gardenlinux_cache_config: GardenlinuxCacheConfig,
    ingress_config: IngressConfig,
):
    helm_values = {
        'external_url': gardenlinux_cache_config.external_url(),
        'imageReference': gardenlinux_cache_config.image_reference(),
        'imageTag': gardenlinux_cache_config.image_tag(),
        'ingress_host': gardenlinux_cache_config.ingress_host(),
        'ingress_issuer_name': ingress_config.issuer_name(),
        'ingress_tls_hosts': ingress_config.tls_host_names(),
        'ingress_ttl': str(ingress_config.ttl()),
        'replicas': gardenlinux_cache_config.replicas(),
        'serviceName': gardenlinux_cache_config.service_name(),
        'servicePort': gardenlinux_cache_config.service_port(),
        'storageSize': gardenlinux_cache_config.volume_size(),
    }
    return helm_values


@ensure_annotations
def deploy_gardenlinux_cache(
    kubernetes_config: KubernetesConfig,
    gardenlinux_cache_config: GardenlinuxCacheConfig,
    chart_dir: str,
    deployment_name: str,
):
    not_empty(deployment_name)

    cfg_factory = global_ctx().cfg_factory()
    chart_dir = os.path.abspath(chart_dir)

    kube_ctx.set_kubecfg(kubernetes_config.kubeconfig())
    ensure_cluster_version(kubernetes_config)

    ingress_config = cfg_factory.ingress(gardenlinux_cache_config.ingress_config())
    helm_values = create_gardenlinux_cache_helm_values(
        gardenlinux_cache_config=gardenlinux_cache_config,
        ingress_config=ingress_config,
    )

    execute_helm_deployment(
        kubernetes_config,
        gardenlinux_cache_config.namespace(),
        chart_dir,
        deployment_name,
        helm_values,
    )
