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
import subprocess
import tempfile
import time
import bcrypt

from ensure import ensure_annotations
from textwrap import dedent
from urllib.parse import urlparse
from subprocess import CalledProcessError

import yaml
import concourse.client as client
import version

from landscape_setup import kube_ctx
from landscape_setup.utils import (
    ensure_helm_setup,
    execute_helm_deployment,
)
from model import (
    ConfigFactory,
    ConfigurationSet,
)
from model.concourse import (
    ConcourseConfig,
    ConcourseApiVersion,
)
from model.container_registry import (
    GcrCredentials,
)
from model.proxy import(
    ProxyConfig
)
from ci.util import (
    ctx as global_ctx,
    not_empty,
    not_none,
    info,
    warning,
    fail,
)


@ensure_annotations
def create_image_pull_secret(
    credentials: GcrCredentials,
    image_pull_secret_name: str,
    namespace: str,
):
    """Create an image pull secret in the K8s cluster to allow pods to download images from gcr"""
    not_none(credentials)
    not_empty(image_pull_secret_name)
    not_empty(namespace)

    ctx = kube_ctx
    namespace_helper = ctx.namespace_helper()
    namespace_helper.create_if_absent(namespace)

    secret_helper = ctx.secret_helper()
    if not secret_helper.get_secret(image_pull_secret_name, namespace):
        secret_helper.create_gcr_secret(
            namespace=namespace,
            name=image_pull_secret_name,
            password=credentials.passwd(),
            user_name=credentials.username(),
            email=credentials.email(),
            server_url=credentials.host(),
        )

        service_account_helper = ctx.service_account_helper()
        service_account_helper.patch_image_pull_secret_into_service_account(
            name="default",
            namespace=namespace,
            image_pull_secret_name=image_pull_secret_name
        )


# Constants related to the MitM-Proxy installation.
# The name under which the config map will be stored in K8s
MITM_CONFIG_CONFIGMAP_NAME = 'mitm-config'


@ensure_annotations
def create_proxy_configmaps(
    proxy_cfg: ProxyConfig,
    namespace: str,
):
    """Create the config map that contains the configuration of the mitm-proxy"""
    not_empty(namespace)

    ctx = kube_ctx
    namespace_helper = ctx.namespace_helper()
    namespace_helper.create_if_absent(namespace)

    config_map_helper = ctx.config_map_helper()

    mitm_proxy_config = proxy_cfg.mitm_proxy().config()

    config_map_helper.create_or_update_config_map(
        namespace=namespace,
        name=MITM_CONFIG_CONFIGMAP_NAME,
        data={
            'config.yaml': yaml.dump(mitm_proxy_config),
        }
    )


def create_instance_specific_helm_values(
    concourse_cfg: ConcourseConfig,
    config_factory: ConfigFactory,
):
    '''
    Creates a dict containing instance specific helm values not explicitly stated in
    the `ConcourseConfig`'s helm_chart_values.
    '''
    not_none(concourse_cfg)

    # 'main'-team credentials need to be included in the values.yaml, unlike the other teams
    concourse_uam_cfg_name = concourse_cfg.concourse_uam_config()
    concourse_uam_cfg = config_factory.concourse_uam(concourse_uam_cfg_name)
    main_team = concourse_uam_cfg.main_team()
    external_url = concourse_cfg.external_url()
    external_host = urlparse(external_url).netloc
    ingress_host = concourse_cfg.ingress_host(config_factory)
    ingress_cfg = config_factory.ingress(concourse_cfg.ingress_config())
    concourse_api_version = concourse_cfg.compatible_api_version(config_factory)

    SUPPORTED_API_VERSIONS = [
        ConcourseApiVersion.V5,
        ConcourseApiVersion.V6_3_0,
        ConcourseApiVersion.V6_5_1
    ]

    if concourse_api_version in SUPPORTED_API_VERSIONS:
        github_config_name = concourse_cfg.github_enterprise_host()
        # 'github_enterprise_host' only configured in case of internal concourse
        # using github enterprise
        if github_config_name:
            github_config = config_factory.github(github_config_name)
            github_http_url = github_config.http_url()
            github_host = urlparse(github_http_url).netloc
        else:
            github_host = None

        bcrypted_pwd = bcrypt.hashpw(
            main_team.password().encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

        instance_specific_values = {
            'concourse': {
                'web': {
                    'externalUrl': external_url,
                    'auth': {
                        'mainTeam': {
                            'localUser': main_team.username(),
                            'github': {
                                'team': main_team.github_auth_team(),
                            },
                        },
                        'github': {
                            'host': github_host
                        },
                    },
                },
            },
            'secrets': {
                'localUsers': main_team.username() + ':' + bcrypted_pwd,
                'githubClientId': main_team.github_auth_client_id(),
                'githubClientSecret': main_team.github_auth_client_secret(),
            },
            'web': {
                'ingress': {
                    'annotations': {
                        'cert.gardener.cloud/issuer': ingress_cfg.issuer_name(),
                        'cert.gardener.cloud/purpose': 'managed',
                        'dns.gardener.cloud/class': 'garden',
                        'dns.gardener.cloud/dnsnames': ingress_host,
                        'dns.gardener.cloud/ttl': str(ingress_cfg.ttl()),
                    },
                    'hosts': [external_host, ingress_host],
                    'tls': [{
                        'secretName': concourse_cfg.tls_secret_name(),
                        'hosts': ingress_cfg.tls_host_names(),
                    }],
                },
            },
        }
    else:
        raise NotImplementedError(
            "Concourse version {v} not supported".format(v=concourse_api_version)
        )

    return instance_specific_values


@ensure_annotations
def add_proxy_values(
    config_set,
    instance_specific_values: dict,
):
    # The dir into which the config map is mounted in the volume.
    # NOTE: This _must_ align with what the mitm is configured to use by our docker image.
    MITM_CONFIG_DIR = '/.mitmproxy'

    # add the sidecar-configuration for the mitm-proxy
    config_factory = global_ctx().cfg_factory()
    concourse_cfg = config_set.concourse()
    secrets_server_cfg = config_set.secrets_server()
    proxy_cfg = config_factory.proxy(concourse_cfg.proxy())
    mitm_cfg = proxy_cfg.mitm_proxy()
    logging_cfg = mitm_cfg.logging()
    sidecar_image_cfg = proxy_cfg.sidecar_image()
    sidecar_containers = [{
        'name': 'setup-iptables-sidecar',
        'image': sidecar_image_cfg.image_reference(),
        'env': [{
            'name': 'PROXY_PORT',
            'value': f'{mitm_cfg.config()["listen_port"]}',
        },{
            'name': 'POD_IP',
            'valueFrom': {
                'fieldRef': {
                    'fieldPath':'status.podIP',
                },
            },
        }],
        'securityContext': {
            'privileged': True,
        },
    },{
        'name': 'mitm-proxy',
        'image': mitm_cfg.image_reference(),
        'env': [{
                'name': 'CONFIG_DIR',
                'value': MITM_CONFIG_DIR,
            },{
                'name': 'SECRETS_SERVER_ENDPOINT',
                'value': secrets_server_cfg.endpoint_url(),
            },{
                'name': 'SECRETS_SERVER_CONCOURSE_CFG_NAME',
                'value': secrets_server_cfg.secrets().concourse_cfg_name(),
            },{
                'name': 'ELASTIC_CONFIG_NAME',
                'value': logging_cfg.els_config_name(),
            },{
                'name': 'ELASTIC_INDEX_NAME',
                'value': logging_cfg.els_index_name(),
            },{
                'name': 'PROXY_CONFIG_NAME',
                'value': proxy_cfg.name(),
        }],
        'ports': [{
            'containerPort': mitm_cfg.config()["listen_port"],
            'hostPort': mitm_cfg.config()["listen_port"],
            'protocol': 'TCP',
        }],
        'volumeMounts': [{
            'name': 'mitm-config',
            'mountPath': MITM_CONFIG_DIR,
        }],
    }]
    additional_volumes = [{
        'name':'mitm-config',
        'configMap': {'name': MITM_CONFIG_CONFIGMAP_NAME},
    }]
    # add new values to dict without replacing existing ones
    vals = instance_specific_values.get('worker', {})
    vals.update(
        {
            'sidecarContainers': sidecar_containers,
            'additionalVolumes': additional_volumes,
        }
    )
    instance_specific_values['worker'] = vals

    return instance_specific_values


@ensure_annotations
def deploy_concourse_landscape(
        config_set: ConfigurationSet,
        deployment_name: str='concourse',
        timeout_seconds: int=180,
):
    ensure_helm_setup()

    # Fetch all the necessary config
    config_factory = global_ctx().cfg_factory()
    concourse_cfg = config_set.concourse()

    # Kubernetes cluster config
    kubernetes_config_name = concourse_cfg.kubernetes_cluster_config()
    kubernetes_config = config_factory.kubernetes(kubernetes_config_name)

    # Container-registry config
    image_pull_secret_name = concourse_cfg.image_pull_secret()
    container_registry = config_factory.container_registry(image_pull_secret_name)
    cr_credentials = container_registry.credentials()

    # Helm config
    helm_chart_default_values_name = concourse_cfg.helm_chart_default_values_config()
    default_helm_values = config_factory.concourse_helmchart(helm_chart_default_values_name).raw
    helm_chart_values_name = concourse_cfg.helm_chart_values()
    custom_helm_values = config_factory.concourse_helmchart(helm_chart_values_name).raw

    # Proxy config
    if concourse_cfg.proxy():
        proxy_cfg_name = concourse_cfg.proxy()
        proxy_cfg = config_factory.proxy(proxy_cfg_name)

        info('Creating config-maps for the mitm proxy ...')
        create_proxy_configmaps(
            proxy_cfg=proxy_cfg,
            namespace=deployment_name,
        )

    info('Creating default image-pull-secret ...')
    create_image_pull_secret(
        credentials=cr_credentials,
        image_pull_secret_name=image_pull_secret_name,
        namespace=deployment_name,
    )

    warning(
        'Teams will not be set up properly on Concourse if the deployment times out, '
        'even if Helm eventually succeeds. In this case, run the deployment command again after '
        'Concourse is available.'
    )

    instance_specific_helm_values = create_instance_specific_helm_values(
        concourse_cfg=concourse_cfg, config_factory=config_factory,
    )
    chart_version = concourse_cfg.helm_chart_version()

    # Add proxy sidecars to instance specific values.
    # NOTE: Only works for helm chart version 3.8.0 or greater
    if concourse_cfg.proxy():
        chart_version_semver = version.parse_to_semver(concourse_cfg.helm_chart_version())
        min_version = version.parse_to_semver('3.8.0')
        if chart_version_semver >= min_version:
            instance_specific_helm_values = add_proxy_values(
                config_set=config_set,
                instance_specific_values=instance_specific_helm_values,
            )
        else:
            fail('Proxy deployment requires the configured helm chart version to be at least 3.8.0')

    execute_helm_deployment(
        kubernetes_config,
        deployment_name,
        'concourse/concourse',
        deployment_name,
        default_helm_values,
        custom_helm_values,
        instance_specific_helm_values,
        chart_version=chart_version,
    )

    info('Waiting until the webserver can be reached ...')
    deployment_helper = kube_ctx.deployment_helper()
    is_web_deployment_available = deployment_helper.wait_until_deployment_available(
        namespace=deployment_name,
        name='concourse-web',
        timeout_seconds=timeout_seconds,
    )
    if not is_web_deployment_available:
        fail(
            dedent(
                """No Concourse webserver reachable after {t} second(s).
                Check status of Pods created by "concourse-web"-deployment in namespace {ns}
                """
            ).format(
                t=timeout_seconds,
                ns=deployment_name,
            )
        )
    info('Webserver became accessible.')

    # Even though the deployment is available, the ingress might need a few seconds to update.
    time.sleep(3)

    info('Setting teams on Concourse ...')
    set_teams(config=concourse_cfg)


def destroy_concourse_landscape(config_name: str, release_name: str):
    # Fetch concourse and kubernetes config
    config_factory = global_ctx().cfg_factory()
    config_set = config_factory.cfg_set(cfg_name=config_name)
    concourse_cfg = config_set.concourse()

    kubernetes_config_name = concourse_cfg.kubernetes_cluster_config()
    kubernetes_config = config_factory.kubernetes(kubernetes_config_name)
    context = kube_ctx
    context.set_kubecfg(kubernetes_config.kubeconfig())

    # Delete helm release
    helm_cmd_path = ensure_helm_setup()
    KUBECONFIG_FILE_NAME = 'kubecfg'
    helm_env = os.environ.copy()
    helm_env['KUBECONFIG'] = KUBECONFIG_FILE_NAME

    with tempfile.TemporaryDirectory() as temp_dir:
        with open(os.path.join(temp_dir, KUBECONFIG_FILE_NAME), 'w') as f:
            yaml.dump(kubernetes_config.kubeconfig(), f)

        try:
            subprocess.run(
                [helm_cmd_path, "delete", release_name],
                env=helm_env,
                check=True,
                cwd=temp_dir
            )
        except CalledProcessError:
            # ignore sporadic connection timeouts from infrastructure
            warning("Connection to K8s cluster lost. Continue with deleting namespace {ns}".format(
                ns=release_name
            ))

    # delete namespace
    namespace_helper = context.namespace_helper()
    namespace_helper.delete_namespace(namespace=release_name)


def set_teams(config: ConcourseConfig):
    not_none(config)
    cfg_factory = global_ctx().cfg_factory()
    concourse_uam_cfg_name = config.concourse_uam_config()
    concourse_uam_cfg = cfg_factory.concourse_uam(concourse_uam_cfg_name)
    # Use main-team, i.e. the team that can change the other teams' credentials
    main_team = concourse_uam_cfg.main_team()

    concourse_api = client.from_cfg(
        concourse_cfg=config,
        team_name=main_team.teamname(),
    )
    for team in concourse_uam_cfg.teams():
        if not team.has_basic_auth_credentials():
            continue
        # We skip the main team here since we cannot update all its credentials at this time.
        if team.teamname() == main_team.teamname():
            continue
        concourse_api.set_team(team)
