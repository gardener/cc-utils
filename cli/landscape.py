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
import os
from util import ctx
from util import (
    info,
    which,
    warning,
    CliHints,
    CliHint,
)
import landscape_setup.concourse as setup_concourse
import landscape_setup.whd as setup_whd
import landscape_setup.monitoring as setup_monitoring


def deploy_or_upgrade_concourse(
    config_name: CliHint(typehint=str, help="the cfg_set to use"),
    deployment_name: CliHint(typehint=str, help="namespace and deployment name")='concourse',
    timeout_seconds: CliHint(typehint=int, help="how long to wait for concourse startup")=180,
    dry_run: bool=True,
):
    '''Deploys a new concourse-instance using the given deployment name and config-directory.'''
    which("helm")

    _display_info(
        dry_run=dry_run,
        operation="DEPLOYED",
        deployment_name=deployment_name,
    )

    if dry_run:
        return

    setup_concourse.deploy_concourse_landscape(
        config_name=config_name,
        deployment_name=deployment_name,
        timeout_seconds=timeout_seconds,
    )


def destroy_concourse(
    config_name: CliHint(typehint=str, help="The config set to use"),
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
        config_name=config_name,
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


def deploy_or_upgrade_webhook_dispatcher(
    cfg_set_name: str,
    chart_dir: CliHints.existing_dir(help="directory of webhook dispatcher chart"),
    deployment_name: str='webhook-dispatcher',
):
    chart_dir = os.path.abspath(chart_dir)

    cfg_factory = ctx().cfg_factory()
    cfg_set = cfg_factory.cfg_set(cfg_set_name)

    webhook_dispatcher_deployment_cfg = cfg_set.webhook_dispatcher_deployment()

    setup_whd.deploy_webhook_dispatcher_landscape(
        cfg_set=cfg_set,
        webhook_dispatcher_deployment_cfg=webhook_dispatcher_deployment_cfg,
        chart_dir=chart_dir,
        deployment_name=deployment_name,
    )


def deploy_or_upgrade_monitoring(
    cfg_set_name: str,
):
    cfg_factory = ctx().cfg_factory()
    setup_monitoring.deploy_monitoring_landscape(
        cfg_set_name=cfg_set_name,
        cfg_factory=cfg_factory,
    )
