# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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
import shutil
import sys
import subprocess
import tempfile

from ensure import ensure_annotations
from string import Template
from urllib.parse import urlparse

import yaml

import util
import kubeutil

import concourse.client as client

from model import (
    ConfigFactory,
    ConfigurationSet,
    ConcourseConfig,
    SecretsServerConfig,
)
from util import ctx as global_ctx, ensure_file_exists, ensure_directory_exists, ensure_not_empty, ensure_not_none
from kubeutil import (
    KubernetesNamespaceHelper,
    KubernetesSecretHelper,
    KubernetesServiceAccountHelper,
    KubernetesDeploymentHelper,
    KubernetesServiceHelper,
)

from kubernetes.client import (
    V1Service, V1ObjectMeta, V1ServiceSpec, V1ServicePort, V1Deployment,
    V1DeploymentSpec, V1PodTemplateSpec, V1PodSpec, V1Container, V1ResourceRequirements, V1ContainerPort,
    V1Probe, V1TCPSocketAction, V1VolumeMount, V1Volume, V1SecretVolumeSource, V1LabelSelector,
    V1beta1Ingress, V1beta1IngressSpec, V1beta1IngressRule, V1beta1HTTPIngressRuleValue, V1beta1HTTPIngressPath,
    V1beta1IngressBackend, V1beta1IngressTLS, V1EnvVar,
)


IMAGE_PULL_SECRET_NAME = "ci-gcr-readonly"
IMAGE_PULL_SECRET_FILE = "ci-gcr-readonly.yml"


@ensure_annotations
def create_image_pull_secret(namespace: str, config_path: str):
    """Create an image pull secret in the K8s cluster to allow pods to download images from gcr"""
    core_api = kubeutil.Ctx().create_core_api()
    secret_helper = KubernetesSecretHelper(core_api)
    gcr = util.parse_yaml_file(os.path.join(config_path, IMAGE_PULL_SECRET_FILE))
    if not secret_helper.get_secret(IMAGE_PULL_SECRET_NAME, namespace):
        secret_helper.create_gcr_secret(
            namespace=namespace,
            name=IMAGE_PULL_SECRET_NAME,
            password=gcr['password'].replace('\n', ''),
            user_name=gcr['user'],
            email=gcr['email'],
            server_url=gcr['host']
        )
        service_accout_helper = KubernetesServiceAccountHelper(core_api)
        service_accout_helper.patch_image_pull_secret_into_service_account(
            name="default",
            namespace=namespace,
            image_pull_secret_name=IMAGE_PULL_SECRET_NAME
        )


@ensure_annotations
def create_tls_secret(namespace: str, config_path: str):
    """Creates the TLS secret for concourse web component in the K8s cluster"""
    # XXX: move those to cc-config
    tls_key_file = "concourse-tls.key"
    tls_cert_file = "concourse-tls.crt"
    tls_secret_name = "concourse-web-tls"

    secret_helper = KubernetesSecretHelper(kubeutil.Ctx().create_core_api())
    if not secret_helper.get_secret(tls_secret_name, namespace):
        tls_key_path = os.path.join(config_path, tls_key_file)
        tls_crt_path = os.path.join(config_path, tls_cert_file)
        util.ensure_file_exists(tls_key_path)
        util.ensure_file_exists(tls_crt_path)

        with open(tls_key_path) as tls_key, open(tls_crt_path) as tls_crt:
            data = {
                "tls.key":tls_key.read(),
                "tls.crt":tls_crt.read()
            }
            secret_helper.put_secret(
                name=tls_secret_name,
                data=data,
                namespace=namespace,
            )


def create_instance_specific_helm_values(concourse_cfg: ConcourseConfig):
    '''
    Creates a dict containing instance specific helm values not explicitly stated in
    the `ConcourseConfig`'s helm_chart_values.
    '''
    ensure_not_none(concourse_cfg)

    # 'main'-team credentials need to be included in the values.yaml, unlike the other teams
    creds = concourse_cfg.team_credentials('main')
    external_url = concourse_cfg.external_url()
    external_host = urlparse(external_url).netloc

    instance_specific_values = {
        'concourse': {
            'username': creds.username(),
            'password': creds.passwd(),
            'githubAuthAuthUrl': creds.github_auth_auth_url(),
            'githubAuthTokenUrl': creds.github_auth_token_url(),
            'githubAuthApiUrl': creds.github_auth_api_url(),
            'githubAuthClientId': creds.github_auth_client_id(),
            'githubAuthClientSecret': creds.github_auth_client_secret(),
            'githubAuthTeam': creds.github_auth_team(),
            'externalURL': external_url,
        },
        'web': {
            'ingress': {
                'hosts': [external_host],
                'tls': [{
                      'secretName': 'concourse-web-tls',
                      'hosts': [external_host],
                      }],
            }
        }
    }
    return instance_specific_values


def deploy_concourse_landscape(
        config_dir: str,
        config_name: str,
        deployment_name: str='concourse',
):
    ensure_directory_exists(config_dir)
    ensure_not_empty(config_name)
    ensure_helm_setup()

    deploy_secrets_server(
        config_dir=config_dir,
        config_name=config_name,
    )
    deploy_delaying_proxy(
        config_dir=config_dir,
        config_name=config_name,
        deployment_name=deployment_name,
    )
    # Concourse is deployed last since Helm will lose connection if deployment takes more than ~60 seconds.
    # Helm will still continue deploying server-side, but the client will report an error.
    deploy_or_upgrade_concourse(
        config_dir=config_dir,
        config_name=config_name,
        deployment_name=deployment_name,
    )


def ensure_helm_setup():
    """Ensure that Helm is installed and its repo-list is up-to-date. Return the path to the found Helm executable"""
    helm_executable = util.which('helm')
    with open(os.devnull) as devnull:
        subprocess.run([helm_executable, 'repo', 'update'], check=True, stdout=devnull)
    return helm_executable


# intentionally hard-coded; review / adjustment of "values.yaml" is required in most cases
# of version upgrades
CONCOURSE_HELM_CHART_VERSION = "1.2.1"

def deploy_or_upgrade_concourse(
        config_dir: str,
        config_name: str,
        deployment_name: str='concourse',
):
    """Deploys (or upgrades) Concourse using the Helm CLI"""
    ensure_directory_exists(config_dir)
    ensure_not_empty(config_name)
    helm_executable = ensure_helm_setup()

    namespace = deployment_name

    config_factory = ConfigFactory.from_cfg_dir(cfg_dir=config_dir)
    configuration_set = config_factory.cfg_set(cfg_name=config_name)
    concourse_cfg = configuration_set.concourse()
    helmchart_cfg_type = 'concourse_helmchart'

    default_helm_values = config_factory._cfg_element(
        cfg_type_name = helmchart_cfg_type,
        cfg_name = concourse_cfg.helm_chart_default_values_config()
    ).raw
    custom_helm_values = config_factory._cfg_element(
        cfg_type_name = helmchart_cfg_type,
        cfg_name = concourse_cfg.helm_chart_values()
    ).raw
    deployment_cfg_dir = os.path.join(config_dir, concourse_cfg.deployment_cfg_dir())

    # create namespace if absent
    namespace_helper = KubernetesNamespaceHelper(kubeutil.Ctx().create_core_api())
    if not namespace_helper.get_namespace(namespace):
        namespace_helper.create_namespace(namespace)

    create_image_pull_secret(namespace, deployment_cfg_dir)
    create_tls_secret(namespace, deployment_cfg_dir)

    DEFAULT_HELM_VALUES_FILE_NAME = 'default_helm_values'
    CUSTOM_HELM_VALUES_FILE_NAME = 'custom_helm_values'
    INSTANCE_SPECIFIC_HELM_VALUES_FILE_NAME = 'instance_specific_helm_values'
    KUBECONFIG_FILE_NAME = 'kubecfg'

    # prepare subprocess args using relative file paths for the values files
    subprocess_args = [
        helm_executable, "upgrade", "--install",
        "--recreate-pods",
        "--wait",
        "--namespace", namespace,
        # Use Helm's value-rendering mechanism to merge the different value-sources.
        # This requires one values-file per source, with later value-files taking precedence.
        "--values", DEFAULT_HELM_VALUES_FILE_NAME,
        "--values", CUSTOM_HELM_VALUES_FILE_NAME,
        "--values", INSTANCE_SPECIFIC_HELM_VALUES_FILE_NAME,
        "--version", CONCOURSE_HELM_CHART_VERSION,
        namespace, # release name is the same as namespace name
        "stable/concourse"
    ]

    helm_env = os.environ.copy()
    # set KUBECONFIG env-var in the copy to relative file path
    helm_env['KUBECONFIG'] = KUBECONFIG_FILE_NAME

    # create temp dir containing all previously referenced files
    with tempfile.TemporaryDirectory() as temp_dir:
        with open(os.path.join(temp_dir, DEFAULT_HELM_VALUES_FILE_NAME), 'w') as f:
            yaml.dump(default_helm_values, f)
        with open(os.path.join(temp_dir, CUSTOM_HELM_VALUES_FILE_NAME), 'w') as f:
            yaml.dump(custom_helm_values, f)
        with open(os.path.join(temp_dir, INSTANCE_SPECIFIC_HELM_VALUES_FILE_NAME), 'w') as f:
            yaml.dump(create_instance_specific_helm_values(concourse_cfg=concourse_cfg), f)
        with open(os.path.join(temp_dir, KUBECONFIG_FILE_NAME), 'w') as f:
            yaml.dump(configuration_set.kubernetes().kubeconfig(), f)

        # run helm from inside the temporary directory so that the prepared file paths work
        subprocess.run(subprocess_args, check=True, cwd=temp_dir, env=helm_env)


def deploy_secrets_server(
        config_dir: str,
        config_name: str,
):
    ensure_directory_exists(config_dir)
    ensure_not_empty(config_name)

    config_factory = ConfigFactory.from_cfg_dir(cfg_dir=config_dir)
    config_set = config_factory.cfg_set(cfg_name=config_name)
    secrets_server_config = config_set.secrets_server()

    ctx = kubeutil.ctx
    service_helper = ctx.service_helper()
    deployment_helper = ctx.deployment_helper()
    secrets_helper = ctx.secret_helper()
    namespace_helper = ctx.namespace_helper()

    namespace = secrets_server_config.namespace()
    namespace_helper.create_if_absent(namespace)

    secret_name = secrets_server_config.secrets().concourse_secret_name()
    # Deploy an empty secret if none exists so that the secrets-server can start.
    # However, if there is already a secret we should not purge its contents.
    if not secrets_helper.get_secret(secret_name, namespace):
        secrets_helper.put_secret(
            name=secret_name,
            data={},
            namespace=namespace,
        )

    service = generate_secrets_server_service(secrets_server_config)
    deployment = generate_secrets_server_deployment(secrets_server_config)

    service_helper.replace_or_create_service(namespace, service)
    deployment_helper.replace_or_create_deployment(namespace, deployment)


def deploy_delaying_proxy(
    config_dir: str,
    config_name: str,
    deployment_name: str,
):
    ensure_directory_exists(config_dir)
    ensure_not_empty(config_name)
    ensure_not_empty(deployment_name)

    config_factory = ConfigFactory.from_cfg_dir(cfg_dir=config_dir)
    config_set = config_factory.cfg_set(cfg_name=config_name)
    concourse_cfg = config_set.concourse()

    ctx = kubeutil.ctx
    service_helper = ctx.service_helper()
    deployment_helper = ctx.deployment_helper()
    namespace_helper = ctx.namespace_helper()
    ingress_helper = ctx.ingress_helper()

    namespace = deployment_name
    namespace_helper.create_if_absent(namespace)

    service = generate_delaying_proxy_service()
    deployment = generate_delaying_proxy_deployment(concourse_cfg)
    ingress = generate_delaying_proxy_ingress(concourse_cfg)

    service_helper.replace_or_create_service(namespace, service)
    deployment_helper.replace_or_create_deployment(namespace, deployment)
    ingress_helper.replace_or_create_ingress(namespace, ingress)


def set_teams(config: ConcourseConfig):
    ensure_not_none(config)

    # Use main-team, i.e. the team that can change the other teams' credentials
    main_team_credentials = config.main_team_credentials()

    concourse_api = client.ConcourseApi(
        base_url=config.external_url(),
        team_name=main_team_credentials.teamname(),
    )
    concourse_api.login(
        team=main_team_credentials.teamname(),
        username=main_team_credentials.username(),
        passwd=main_team_credentials.passwd(),
    )
    for team in config.all_team_credentials():
        # We skip the main team here since we cannot update all its credentials at this time.
        if team.teamname == "main":
            continue
        concourse_api.set_team(team)


def generate_secrets_server_service(
    secrets_server_config: SecretsServerConfig,
):
    ensure_not_none(secrets_server_config)

    # We need to ensure that the labels and selectors match between the deployment and the service,
    # therefore we base them on the configured service name.
    service_name = secrets_server_config.service_name()
    selector = {'app':service_name}

    return V1Service(
        kind='Service',
        metadata=V1ObjectMeta(
            name=service_name,
        ),
        spec=V1ServiceSpec(
            type='ClusterIP',
            ports=[
                V1ServicePort(protocol='TCP', port=80, target_port=8080),
            ],
            selector=selector,
            session_affinity='None',
        ),
    )


def generate_secrets_server_deployment(
    secrets_server_config: SecretsServerConfig,
):
    ensure_not_none(secrets_server_config)

    service_name = secrets_server_config.service_name()
    secret_name = secrets_server_config.secrets().concourse_secret_name()
    # We need to ensure that the labels and selectors match for both the deployment and the service,
    # therefore we base them on the configured service name.
    labels={'app':service_name}

    return V1Deployment(
        kind='Deployment',
        metadata=V1ObjectMeta(
            name=service_name,
            labels=labels
        ),
        spec=V1DeploymentSpec(
            replicas=1,
            selector=V1LabelSelector(match_labels=labels),
            template=V1PodTemplateSpec(
                metadata=V1ObjectMeta(labels=labels),
                spec=V1PodSpec(
                    containers=[
                        V1Container(
                            image='eu.gcr.io/gardener-project/cc/job-image:0.20.0',
                            image_pull_policy='IfNotPresent',
                            name='secrets-server',
                            resources=V1ResourceRequirements(
                                requests={'cpu':'50m', 'memory': '50Mi'},
                                limits={'cpu':'50m', 'memory': '50Mi'},
                            ),
                            command=['bash'],
                            args=[
                                '-c',
                                '''
                                # switch to secrets serving directory (create it if missing, i.e. if no other secrets are mounted there)
                                mkdir -p /secrets && cd /secrets
                                # make Kubernetes serviceaccount secrets available by default
                                cp -r /var/run/secrets/kubernetes.io/serviceaccount serviceaccount
                                # store Kubernetes service endpoint env as file for consumer
                                env | grep KUBERNETES_SERVICE > serviceaccount/env
                                # launch minimalistic python server in that directory serving requests across all network interfaces
                                python3 -m http.server 8080
                                '''
                            ],
                            ports=[
                                V1ContainerPort(container_port=8080),
                            ],
                            liveness_probe=V1Probe(
                                tcp_socket=V1TCPSocketAction(port=8080),
                                initial_delay_seconds=10,
                                period_seconds=10,
                            ),
                            volume_mounts=[
                                V1VolumeMount(
                                    name=secret_name,
                                    mount_path='/secrets/concourse-secrets',
                                    read_only=True,
                                ),
                            ],
                        ),
                    ],
                    volumes=[
                        V1Volume(
                            name=secret_name,
                            secret=V1SecretVolumeSource(
                                secret_name=secret_name,
                            )
                        )
                    ]
                )
            )
        )
    )


def generate_delaying_proxy_deployment(concourse_cfg: ConcourseConfig):
    ensure_not_none(concourse_cfg)

    external_url = concourse_cfg.external_url()
    label = {'app':'delaying-proxy'}

    return V1Deployment(
        kind='Deployment',
        metadata=V1ObjectMeta(name='delaying-proxy'),
        spec=V1DeploymentSpec(
            replicas=1,
            selector=V1LabelSelector(match_labels=label),
            template=V1PodTemplateSpec(
                metadata=V1ObjectMeta(labels=label),
                spec=V1PodSpec(
                    containers=[
                        V1Container(
                            image='eu.gcr.io/gardener-project/cc/github-enterprise-proxy:0.1.0',
                            image_pull_policy='IfNotPresent',
                            name='delaying-proxy',
                            ports=[
                                V1ContainerPort(container_port=8080),
                            ],
                            liveness_probe=V1Probe(
                                tcp_socket=V1TCPSocketAction(port=8080),
                                initial_delay_seconds=10,
                                period_seconds=10,
                            ),
                            env=[
                                V1EnvVar(name='CONCOURSE_URL', value=external_url),
                            ],
                        ),
                    ],
                )
            )
        )
    )


def generate_delaying_proxy_ingress(concourse_cfg: ConcourseConfig):
    ensure_not_none(concourse_cfg)

    proxy_url = concourse_cfg.proxy_url()
    host = urlparse(proxy_url).netloc

    return V1beta1Ingress(
        kind='Ingress',
        metadata=V1ObjectMeta(
            name='delaying-proxy',
            annotations={'kubernetes.io/ingress.class':'nginx'},
        ),
        spec=V1beta1IngressSpec(
            rules=[
                V1beta1IngressRule(
                    host=host,
                    http=V1beta1HTTPIngressRuleValue(
                        paths=[
                            V1beta1HTTPIngressPath(
                                backend=V1beta1IngressBackend(
                                    service_name='delaying-proxy-svc',
                                    service_port=80,
                                ),
                            ),
                        ],
                    ),
                ),
            ],
            tls=[
                V1beta1IngressTLS(
                    hosts=[host],
                    secret_name='concourse-web-tls'
                ),
            ],
        ),
    )


def generate_delaying_proxy_service():
    return V1Service(
        kind='Service',
        metadata=V1ObjectMeta(
            name='delaying-proxy-svc',
            labels={'app':'delaying-proxy-svc'},
        ),
        spec=V1ServiceSpec(
            type='ClusterIP',
            ports=[
                V1ServicePort(name='default', protocol='TCP', port=80, target_port=8080),
            ],
            selector={'app':'delaying-proxy'},
            session_affinity='None',
        ),
    )
