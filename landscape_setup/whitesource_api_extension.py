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

from landscape_setup import kube_ctx
from landscape_setup.utils import (
    execute_helm_deployment,
)
from model.whitesource import (
    WhitesourceApiExtensionDeploymentConfig
)
from ci.util import (
    ctx as global_ctx,
    not_empty,
)


@ensure_annotations
def deploy_webhook_dispatcher_landscape(
    whitesource_api_extension_deployment_cfg: WhitesourceApiExtensionDeploymentConfig,
    chart_dir: str,
    deployment_name: str,
):
    not_empty(deployment_name)

    chart_dir = os.path.abspath(chart_dir)
    cfg_factory = global_ctx().cfg_factory()

    # Set the global context to the cluster specified in KubernetesConfig
    kubernetes_config_name = whitesource_api_extension_deployment_cfg.kubernetes_config_name()
    kubernetes_config = cfg_factory.kubernetes(kubernetes_config_name)
    kube_ctx.set_kubecfg(kubernetes_config.kubeconfig())

    kubernetes_cfg_name = whitesource_api_extension_deployment_cfg.kubernetes_config_name()
    kubernetes_cfg = cfg_factory.kubernetes(kubernetes_cfg_name)

    execute_helm_deployment(
        kubernetes_config=kubernetes_cfg,
        namespace=deployment_name,
        chart_name=chart_dir,
        release_name=deployment_name,
    )
