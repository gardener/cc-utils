# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
import os

import ci.util
import gitutil

from tempfile import TemporaryDirectory
from landscape_setup.utils import execute_helm_deployment
from model.container_registry import find_config


CLAMAV_HELMCHART_REPO_PATH = 'MalwareScanning/helmchart'
CLAMAV_GITHUB_CONFIG = 'github_wdf_sap_corp'


def deploy_clam_av(
    clamav_cfg_name,
    kubernetes_cfg_name,
):
    cfg_factory = ci.util.ctx().cfg_factory()
    clamav_config = cfg_factory.clamav(clamav_cfg_name)
    kubernetes_config = cfg_factory.kubernetes(kubernetes_cfg_name)
    clamav_deployment_name = clamav_config.namespace()

    with TemporaryDirectory() as temp_dir:
        from_github_cfg = cfg_factory.github(CLAMAV_GITHUB_CONFIG)
        gitutil.GitHelper.clone_into(
            target_directory=temp_dir,
            github_cfg=from_github_cfg,
            github_repo_path=CLAMAV_HELMCHART_REPO_PATH,
        )
        execute_helm_deployment(
            kubernetes_config,
            clamav_config.namespace(),
            f'{os.path.join(temp_dir, "clamav")}',
            clamav_deployment_name,
            create_clamav_helm_values(clamav_cfg_name),
        )


def create_clamav_helm_values(clamav_cfg_name):
    cfg_factory = ci.util.ctx().cfg_factory()
    clamav_config = cfg_factory.clamav(clamav_cfg_name)
    clamav_image_config = clamav_config.clamav_image_config()
    freshclam_image_config = clamav_config.freshclam_image_config()
    clamav_image_name = clamav_image_config.image_name()
    helm_values = {
        'clamAV': {
            'replicas': clamav_config.replicas(),
            'serviceName': clamav_config.service_name(),
            'servicePort': clamav_config.service_port(),
            'imageReference': clamav_image_config.image_name(),
            'imageTag': clamav_image_config.image_tag(),
            'configValues': clamav_config.clamd_config_values(),
        },
        'freshClam': {
            'imageReference': freshclam_image_config.image_name(),
            'imageTag': freshclam_image_config.image_tag(),
        }
    }
    container_registry_config = find_config(clamav_image_name)
    if container_registry_config:
        credentials = container_registry_config.credentials()
        helm_values['imageCredentials'] = {
            'registry': credentials.host(),
            'username': credentials.username(),
            'password': credentials.passwd(),
        }
    return helm_values
