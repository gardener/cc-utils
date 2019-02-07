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

import util
from landscape_setup import kube_ctx
from landscape_setup.utils import (
    ensure_helm_setup,
    ensure_cluster_version,
    create_tls_secret,
)
from model import (
    ConfigFactory,
)
from model.webhook_dispatcher import (
    WebhookDispatcherDeploymentConfig
)
from model.kubernetes import (
    KubernetesConfig,
)
from util import (
    ctx as global_ctx,
    not_empty,
    info,
)


@ensure_annotations
def create_webhook_dispatcher_helm_values(
    cfg_set,
    webhook_dispatcher_deployment_cfg: WebhookDispatcherDeploymentConfig,
    cfg_factory: ConfigFactory,
):
    # calculate secrets server endpoint
    secrets_server_name = webhook_dispatcher_deployment_cfg.secrets_server_config_name()
    secrets_server_cfg = cfg_factory.secrets_server(secrets_server_name)
    secrets_server_endpoint = secrets_server_cfg.endpoint_url()
    secrets_server_concourse_cfg_name = '/'.join([
        secrets_server_cfg.secrets().concourse_secret_name(),
        secrets_server_cfg.secrets().concourse_attribute()])
    container_port = webhook_dispatcher_deployment_cfg.webhook_dispatcher_container_port()

    env_vars = []
    env_vars.append({
        'name': 'SECRETS_SERVER_ENDPOINT', 'value': secrets_server_endpoint
    })
    env_vars.append({
        'name': 'SECRETS_SERVER_CONCOURSE_CFG_NAME', 'value': secrets_server_concourse_cfg_name
    })

    cmd_args = [
        '--webhook-dispatcher-cfg-name',
        webhook_dispatcher_deployment_cfg.webhook_dispatcher_config_name(),
        '--port',
        f'"{container_port}"',
        '--cfg-set-name',
        cfg_set.name(),
    ]

    helm_values = {
        'ingress_host': webhook_dispatcher_deployment_cfg.ingress_host(),
        'tls_name': webhook_dispatcher_deployment_cfg.tls_config_name(),
        'image_reference': webhook_dispatcher_deployment_cfg.image_reference(),
        'cmd_args': cmd_args,
        'env_vars': env_vars,
        'webhook_dispatcher_port': container_port,
    }

    return helm_values


@ensure_annotations
def deploy_webhook_dispatcher_landscape(
    cfg_set,
    webhook_dispatcher_deployment_cfg: WebhookDispatcherDeploymentConfig,
    chart_dir: str,
    deployment_name: str,
):
    not_empty(deployment_name)

    chart_dir = os.path.abspath(chart_dir)
    cfg_factory = global_ctx().cfg_factory()

    # Set the global context to the cluster specified in KubernetesConfig
    kubernetes_config_name = webhook_dispatcher_deployment_cfg.kubernetes_config_name()
    kubernetes_config = cfg_factory.kubernetes(kubernetes_config_name)
    kube_ctx.set_kubecfg(kubernetes_config.kubeconfig())

    ensure_cluster_version(kubernetes_config)

    # TLS config
    tls_config_name = webhook_dispatcher_deployment_cfg.tls_config_name()
    tls_config = cfg_factory.tls_config(tls_config_name)
    tls_secret_name = "webhook-dispatcher-tls"

    info('Creating tls-secret ...')
    create_tls_secret(
        tls_config=tls_config,
        tls_secret_name=tls_secret_name,
        namespace=deployment_name,
    )

    kubernetes_cfg_name = webhook_dispatcher_deployment_cfg.kubernetes_config_name()
    kubernetes_cfg = cfg_factory.kubernetes(kubernetes_cfg_name)

    deploy_or_upgrade_webhook_dispatcher(
        cfg_set=cfg_set,
        webhook_dispatcher_deployment_cfg=webhook_dispatcher_deployment_cfg,
        chart_dir=chart_dir,
        cfg_factory=cfg_factory,
        kubernetes_cfg=kubernetes_cfg,
        deployment_name=deployment_name,
    )


@ensure_annotations
def deploy_or_upgrade_webhook_dispatcher(
        cfg_set,
        webhook_dispatcher_deployment_cfg: WebhookDispatcherDeploymentConfig,
        chart_dir: str,
        cfg_factory: ConfigFactory,
        kubernetes_cfg: KubernetesConfig,
        deployment_name: str='webhook-dispatcher',
):
    """Deploys (or upgrades) webhook dispatcher via Helm CLI"""
    helm_executable = ensure_helm_setup()
    chart_dir = util.existing_dir(chart_dir)
    namespace = not_empty(deployment_name)

    # create namespace if absent
    namespace_helper = kube_ctx.namespace_helper()
    if not namespace_helper.get_namespace(namespace):
        namespace_helper.create_namespace(namespace)

    WEBHOOK_DISPATCHER_HELM_VALUES_FILE_NAME = 'webhook_dispatcher_helm_values'
    KUBECONFIG_FILE_NAME = 'kubecfg'

    # prepare subprocess args using relative file paths for the values files
    subprocess_args = [
        helm_executable, "upgrade", "--install", "--force",
        "--recreate-pods",
        "--wait",
        "--namespace", namespace,
        # Use Helm's value-rendering mechanism to merge value-sources.
        "--values", WEBHOOK_DISPATCHER_HELM_VALUES_FILE_NAME,
        namespace, # release name is the same as namespace name
        chart_dir,
    ]

    helm_env = os.environ.copy()

    # create temp dir containing all previously referenced files
    with tempfile.TemporaryDirectory() as temp_dir:
        kubeconfig_path = os.path.join(temp_dir, KUBECONFIG_FILE_NAME)
        helm_env['KUBECONFIG'] = kubeconfig_path
        with open(os.path.join(temp_dir, WEBHOOK_DISPATCHER_HELM_VALUES_FILE_NAME), 'w') as f:
            yaml.dump(
                create_webhook_dispatcher_helm_values(
                    cfg_set=cfg_set,
                    webhook_dispatcher_deployment_cfg=webhook_dispatcher_deployment_cfg,
                    cfg_factory=cfg_factory,
                ),
                f
            )
        with open(kubeconfig_path, 'w') as f:
            yaml.dump(kubernetes_cfg.kubeconfig(), f)

        # run helm from inside the temporary directory so that the prepared file paths work
        subprocess.run(subprocess_args, check=True, cwd=temp_dir, env=helm_env)
