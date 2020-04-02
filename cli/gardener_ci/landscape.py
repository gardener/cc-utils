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
import enum
import os

from ci.util import (
    ctx,
    existing_dir,
    info,
    which,
    warning,
    CliHints,
    CliHint,
)
from landscape_setup import kube_ctx
from landscape_setup.utils import (
    ensure_cluster_version,
    ensure_helm_setup,
)
import landscape_setup.clamav as setup_clamav
import landscape_setup.concourse as setup_concourse
import landscape_setup.monitoring as setup_monitoring
import landscape_setup.secrets_server as setup_secrets_server
import landscape_setup.whd as setup_whd


class LandscapeComponent(enum.Enum):
    CONCOURSE = 'concourse'
    SECRETS_SERVER = 'secrets_server'
    WHD = 'webhook_dispatcher'
    MONITORING = 'monitoring'
    CLAMAV = 'clam_av'


CONFIG_SET_HELP = (
    "Name of the config set to use. All further configuration (e.g.: Concourse config) needed "
    "for deployment will be pulled from the config set with the given name."
)


def deploy_or_upgrade_landscape(
    config_set_name: CliHint(typehint=str, help=CONFIG_SET_HELP),
    components: CliHint(
        type=LandscapeComponent,
        typehint=[LandscapeComponent],
        choices=[component for component in LandscapeComponent],
        help="list of components to deploy. By default, ALL components will be deployed."
    )=None,
    webhook_dispatcher_chart_dir: CliHint(
        typehint=str,
        help="directory of webhook dispatcher chart",
    )=None,
    concourse_deployment_name: CliHint(
        typehint=str, help="namespace and deployment name for Concourse"
    )='concourse',
    timeout_seconds: CliHint(typehint=int, help="how long to wait for concourse startup")=180,
    webhook_dispatcher_deployment_name: str='webhook-dispatcher',
    dry_run: bool=True,
):
    '''Deploys the given components of the Concourse landscape.
    '''
    # handle default (all known components)
    if not components:
        components = [component for component in LandscapeComponent]
    # Validate
    if LandscapeComponent.WHD in components:
        if not webhook_dispatcher_chart_dir:
            raise ValueError(
                f"--webhook-dispatcher-chart-dir must be given if component "
                f"'{LandscapeComponent.WHD.value}' is to be deployed."
            )
        else:
            webhook_dispatcher_chart_dir = existing_dir(webhook_dispatcher_chart_dir)

    _display_info(
        dry_run=dry_run,
        operation="DEPLOYED",
        deployment_name=concourse_deployment_name,
        components=components,
    )

    if dry_run:
        return

    cfg_factory = ctx().cfg_factory()
    config_set = cfg_factory.cfg_set(config_set_name)
    concourse_cfg = config_set.concourse()

    # Set the global kubernetes cluster context to the cluster specified in the ConcourseConfig
    kubernetes_config_name = concourse_cfg.kubernetes_cluster_config()
    kubernetes_cfg = cfg_factory.kubernetes(kubernetes_config_name)
    kube_ctx.set_kubecfg(kubernetes_cfg.kubeconfig())
    ensure_cluster_version(kubernetes_cfg)

    if LandscapeComponent.SECRETS_SERVER in components:
        info('Deploying Secrets Server')
        deploy_secrets_server(
            config_set_name=config_set_name,
        )

    if LandscapeComponent.CONCOURSE in components:
        info('Deploying Concourse')
        deploy_or_upgrade_concourse(
            config_set_name=config_set_name,
            deployment_name=concourse_deployment_name,
            timeout_seconds=timeout_seconds,
        )

    if LandscapeComponent.WHD in components:
        info('Deploying Webhook Dispatcher')
        deploy_or_upgrade_webhook_dispatcher(
            config_set_name=config_set_name,
            chart_dir=webhook_dispatcher_chart_dir,
            deployment_name=webhook_dispatcher_deployment_name,
        )

    if LandscapeComponent.MONITORING in components:
        info('Deploying Monitoring stack')
        deploy_or_upgrade_monitoring(
            config_set_name=config_set_name,
        )

    if LandscapeComponent.CLAMAV in components:
        info ('Deploying ClamAV')
        deploy_or_upgrade_clamav(
            config_set_name=config_set_name,
        )


def deploy_or_upgrade_concourse(
    config_set_name: CliHint(typehint=str, help=CONFIG_SET_HELP),
    deployment_name: CliHint(typehint=str, help="namespace and deployment name")='concourse',
    timeout_seconds: CliHint(typehint=int, help="how long to wait for concourse startup")=180,
):
    '''Deploys a new concourse-instance using the given deployment name and config-directory.'''
    ensure_helm_setup()
    cfg_factory = ctx().cfg_factory()
    config_set = cfg_factory.cfg_set(config_set_name)

    setup_concourse.deploy_concourse_landscape(
        config_set=config_set,
        deployment_name=deployment_name,
        timeout_seconds=timeout_seconds,
    )


def destroy_concourse(
    config_set_name: CliHint(typehint=str, help=CONFIG_SET_HELP),
    release_name: CliHint(typehint=str, help="namespace and deployment name")='concourse',
    dry_run: bool = True
):
    '''Destroys a concourse-instance using the given helm release name'''

    _display_info(
        dry_run=dry_run,
        operation="DESTROYED",
        deployment_name=release_name,
    )

    if dry_run:
        return

    setup_concourse.destroy_concourse_landscape(
        config_name=config_set_name,
        release_name=release_name
    )


def _display_info(dry_run: bool, operation: str, **kwargs):
    info("Concourse will be {o} using helm with the following arguments".format(o=operation))
    max_leng = max(map(len, kwargs.keys()))
    for k, v in kwargs.items():
        key_str = k.ljust(max_leng)
        info("{k}: {v}".format(k=key_str, v=v))

    if dry_run:
        warning("this was a --dry-run. Set the --no-dry-run flag to actually deploy")


def deploy_secrets_server(
    config_set_name: CliHint(typehint=str, help=CONFIG_SET_HELP),
):
    cfg_factory = ctx().cfg_factory()
    config_set = cfg_factory.cfg_set(config_set_name)
    secrets_server_config = config_set.secrets_server()

    setup_secrets_server.deploy_secrets_server(
        secrets_server_config=secrets_server_config,
    )


def deploy_or_upgrade_webhook_dispatcher(
    config_set_name: CliHint(typehint=str, help=CONFIG_SET_HELP),
    chart_dir: CliHints.existing_dir(help="directory of webhook dispatcher chart"),
    deployment_name: str='webhook-dispatcher',
):
    chart_dir = os.path.abspath(chart_dir)

    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(config_set_name)

    webhook_dispatcher_deployment_cfg = cfg_set.webhook_dispatcher_deployment()

    setup_whd.deploy_webhook_dispatcher_landscape(
        cfg_set=cfg_set,
        webhook_dispatcher_deployment_cfg=webhook_dispatcher_deployment_cfg,
        chart_dir=chart_dir,
        deployment_name=deployment_name,
    )


def deploy_or_upgrade_monitoring(
    config_set_name: CliHint(typehint=str, help=CONFIG_SET_HELP),
):
    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(config_set_name)
    setup_monitoring.deploy_monitoring_landscape(
        cfg_set=cfg_set,
        cfg_factory=cfg_factory,
    )


def deploy_or_upgrade_clamav(
    config_set_name: CliHint(typehint=str, help=CONFIG_SET_HELP),
):
    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(config_set_name)
    concourse_cfg = cfg_set.concourse()
    kubernetes_cfg_name = concourse_cfg.kubernetes_cluster_config()
    clamav_cfg_name = concourse_cfg.clamav_config()
    if clamav_cfg_name is not None:
        setup_clamav.deploy_clam_av(
            clamav_cfg_name=clamav_cfg_name,
            kubernetes_cfg_name=kubernetes_cfg_name,
        )
    else:
        info(
            f"No ClamAV configured for the Concourse in config set '{config_set_name}'. Will "
            "not deploy ClamAV."
        )
