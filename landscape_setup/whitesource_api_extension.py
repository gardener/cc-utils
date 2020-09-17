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

from landscape_setup import (
    kube_ctx,
    paths,
)
from landscape_setup.utils import (
    execute_helm_deployment,
)
from model.kubernetes import (
    KubernetesConfig
)
from model.whitesource import (
    WhitesourceConfig
)
from ci.util import (
    not_empty,
)


@ensure_annotations
def deploy_whitesource_api_extension(
    whitesource_cfg: WhitesourceConfig,
    kubernetes_cfg: KubernetesConfig,
    chart_dir: str = os.path.join(paths.chartdirt, 'whitesource-api-extension'),
    deployment_name: str = 'whitesource-api-extension',
):
    not_empty(deployment_name)

    # Set the global context to the cluster specified in KubernetesConfig
    kube_ctx.set_kubecfg(kubernetes_cfg)

    execute_helm_deployment(
        kubernetes_config=kubernetes_cfg,
        namespace=whitesource_cfg.namespace(),
        chart_name=chart_dir,
        release_name=deployment_name,
    )
