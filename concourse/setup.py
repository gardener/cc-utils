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
import time

from ensure import ensure_annotations
from textwrap import dedent
from urllib.parse import urlparse
from subprocess import CalledProcessError

import yaml

import util
import kubeutil

import concourse.client as client

from model import (
    SecretsServerConfig,
    TlsConfig,
    GcrCredentials,
    KubernetesConfig,
    NamedModelElement,
)
from model.concourse import (
    ConcourseConfig,
)
from util import (
    ctx as global_ctx,
    not_empty,
    not_none,
    info,
    warning,
    fail,
    which,
    create_url_from_attributes,
)

from kubernetes.client import (
    V1Service,
    V1ObjectMeta,
    V1ServiceSpec,
    V1ServicePort,
    V1Deployment,
    V1DeploymentSpec,
    V1PodTemplateSpec,
    V1PodSpec,
    V1Container,
    V1ResourceRequirements,
    V1ContainerPort,
    V1Probe,
    V1TCPSocketAction,
    V1VolumeMount,
    V1Volume,
    V1SecretVolumeSource,
    V1LabelSelector,
    V1beta1Ingress,
    V1beta1IngressSpec,
    V1beta1IngressRule,
    V1beta1HTTPIngressRuleValue,
    V1beta1HTTPIngressPath,
    V1beta1IngressBackend,
    V1beta1IngressTLS,
    V1EnvVar,
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

    ctx = kubeutil.ctx
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


@ensure_annotations
def create_tls_secret(
    tls_config: TlsConfig,
    tls_secret_name: str,
    namespace: str,
):
    """Creates the configured TLS secret for the Concourse web-component in the K8s cluster"""
    not_none(tls_config)
    not_empty(tls_secret_name)
    not_empty(namespace)

    ctx = kubeutil.ctx
    namespace_helper = ctx.namespace_helper()
    namespace_helper.create_if_absent(namespace)

    secret_helper = ctx.secret_helper()
    if not secret_helper.get_secret(tls_secret_name, namespace):
        data = {
            'tls.key':tls_config.private_key(),
            'tls.crt':tls_config.certificate(),
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
    not_none(concourse_cfg)

    # 'main'-team credentials need to be included in the values.yaml, unlike the other teams
    creds = concourse_cfg.team_credentials('main')
    external_url = concourse_cfg.external_url()
    external_host = urlparse(external_url).netloc
    concourse_tls_secret_name = concourse_cfg.tls_secret_name()
    ingress_host = concourse_cfg.ingress_host()

    instance_specific_values = {
        'concourse': {
            'externalURL': external_url,
            'githubAuth': {
                'team': creds.github_auth_team(),
                'authUrl': creds.github_auth_auth_url(),
                'tokenUrl': creds.github_auth_token_url(),
                'apiUrl': creds.github_auth_api_url(),
            }
        },
        'secrets': {
            'basicAuthUsername': creds.username(),
            'basicAuthPassword': creds.passwd(),
            'githubAuthClientId': creds.github_auth_client_id(),
            'githubAuthClientSecret': creds.github_auth_client_secret(),
        },
        'web': {
            'ingress': {
                'hosts': [external_host, ingress_host],
                'tls': [{
                      'secretName': concourse_tls_secret_name,
                      'hosts': [external_host, ingress_host],
                      }],
            }
        }
    }
    return instance_specific_values


def deploy_concourse_landscape(
        config_name: str,
        deployment_name: str='concourse',
        timeout_seconds: int='180'
):
    not_empty(config_name)
    ensure_helm_setup()

    # Fetch all the necessary config
    config_factory = global_ctx().cfg_factory()
    config_set = config_factory.cfg_set(cfg_name=config_name)
    concourse_cfg = config_set.concourse()

    # Set the global context to the cluster specified in the ConcourseConfig
    kubernetes_config_name = concourse_cfg.kubernetes_cluster_config()
    kubernetes_config = config_factory.kubernetes(kubernetes_config_name)
    kubeutil.ctx.set_kubecfg(kubernetes_config.kubeconfig())

    ensure_cluster_version(kubernetes_config)

    # Container-registry config
    image_pull_secret_name = concourse_cfg.image_pull_secret()
    container_registry = config_factory.container_registry(image_pull_secret_name)
    cr_credentials = container_registry.credentials()

    # TLS config
    tls_config_name = concourse_cfg.tls_config()
    tls_config = config_factory.tls_config(tls_config_name)
    tls_secret_name = concourse_cfg.tls_secret_name()

    # Secrets server
    secrets_server_config = config_set.secrets_server()

    # Helm config
    helm_chart_default_values_name = concourse_cfg.helm_chart_default_values_config()
    default_helm_values = config_factory.concourse_helmchart(helm_chart_default_values_name).raw
    helm_chart_values_name = concourse_cfg.helm_chart_values()
    custom_helm_values = config_factory.concourse_helmchart(helm_chart_values_name).raw

    info('Creating default image-pull-secret ...')
    create_image_pull_secret(
        credentials=cr_credentials,
        image_pull_secret_name=image_pull_secret_name,
        namespace=deployment_name,
    )

    info('Creating tls-secret ...')
    create_tls_secret(
        tls_config=tls_config,
        tls_secret_name=tls_secret_name,
        namespace=deployment_name,
    )

    info('Deploying secrets-server ...')
    deploy_secrets_server(
        secrets_server_config=secrets_server_config,
    )

    if concourse_cfg.deploy_delaying_proxy():
        info('Deploying delaying proxy ...')
        deploy_delaying_proxy(
            concourse_cfg=concourse_cfg,
            deployment_name=deployment_name,
        )

    info('Deploying Concourse ...')
    # Concourse is deployed last since Helm will lose connection if deployment takes more
    # than ~60 seconds.
    # Helm will still continue deploying server-side, but the client will report an error.
    deploy_or_upgrade_concourse(
        default_helm_values=default_helm_values,
        custom_helm_values=custom_helm_values,
        concourse_cfg=concourse_cfg,
        kubernetes_config=kubernetes_config,
        deployment_name=deployment_name,
    )

    info('Waiting until the webserver can be reached ...')
    deployment_helper = kubeutil.ctx.deployment_helper()
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
                t = timeout_seconds,
                ns = deployment_name,
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
    context = kubeutil.ctx
    context.set_kubecfg(kubernetes_config.kubeconfig())

    # Delete helm release
    helm_cmd_path = which("helm")
    KUBECONFIG_FILE_NAME = 'kubecfg'
    helm_env = os.environ.copy()
    helm_env['KUBECONFIG'] = KUBECONFIG_FILE_NAME

    with tempfile.TemporaryDirectory() as temp_dir:
        with open(os.path.join(temp_dir, KUBECONFIG_FILE_NAME), 'w') as f:
            yaml.dump(kubernetes_config.kubeconfig(), f)

        try:
            subprocess.run(
                [helm_cmd_path, "delete", release_name, "--purge"],
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


# pylint: disable=no-member
def ensure_cluster_version(kubernetes_config: KubernetesConfig):
    not_none(kubernetes_config)

    cluster_version_info = kubeutil.get_cluster_version_info()
    configured_version_info = kubernetes_config.cluster_version()

    if (
        cluster_version_info.major != configured_version_info['major'] or
        cluster_version_info.minor != configured_version_info['minor']
    ):
        fail(
            'cluster version mismatch "Major: {a_major} Minor: '
            '{a_minor}". Expected "Major: {e_major} Minor: {e_minor}".'.format(
                a_major=cluster_version_info.major,
                a_minor=cluster_version_info.minor,
                e_major=configured_version_info['major'],
                e_minor=configured_version_info['minor'],
            )
        )
# pylint: enable=no-member


def ensure_helm_setup():
    """Ensure up-to-date helm installation. Return the path to the found Helm executable"""
    helm_executable = util.which('helm')
    with open(os.devnull) as devnull:
        subprocess.run([helm_executable, 'repo', 'update'], check=True, stdout=devnull)
    return helm_executable


def deploy_or_upgrade_concourse(
        default_helm_values: NamedModelElement,
        custom_helm_values: NamedModelElement,
        concourse_cfg: ConcourseConfig,
        kubernetes_config: KubernetesConfig,
        deployment_name: str='concourse',
):
    """Deploys (or upgrades) Concourse using the Helm CLI"""
    not_none(default_helm_values)
    not_none(custom_helm_values)
    not_none(concourse_cfg)
    helm_executable = ensure_helm_setup()
    helm_chart_version = concourse_cfg.helm_chart_version()
    not_none(helm_chart_version)

    namespace = deployment_name

    # create namespace if absent
    namespace_helper = kubeutil.ctx.namespace_helper()
    if not namespace_helper.get_namespace(namespace):
        namespace_helper.create_namespace(namespace)

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
        "--version", helm_chart_version,
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
            yaml.dump(kubernetes_config.kubeconfig(), f)

        # run helm from inside the temporary directory so that the prepared file paths work
        subprocess.run(subprocess_args, check=True, cwd=temp_dir, env=helm_env)


def deploy_secrets_server(secrets_server_config: SecretsServerConfig):
    not_none(secrets_server_config)

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
    concourse_cfg: ConcourseConfig,
    deployment_name: str,
):
    not_none(concourse_cfg)
    not_empty(deployment_name)

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
    not_none(config)

    # Use main-team, i.e. the team that can change the other teams' credentials
    main_team_credentials = config.main_team_credentials()

    # use ingress_host instead of external_url which points to a possibly not yet switched CNAME
    base_url = create_url_from_attributes(config.ingress_host())

    concourse_api = client.ConcourseApi(
        base_url=base_url,
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
    not_none(secrets_server_config)

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
    not_none(secrets_server_config)

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
                                # chdir to secrets dir; create if absent
                                mkdir -p /secrets && cd /secrets
                                # make Kubernetes serviceaccount secrets available by default
                                cp -r /var/run/secrets/kubernetes.io/serviceaccount serviceaccount
                                # store Kubernetes service endpoint env as file for consumer
                                env | grep KUBERNETES_SERVICE > serviceaccount/env
                                # launch secrets server serving secrets dir contents on all IFs
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
    not_none(concourse_cfg)

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
                            image='eu.gcr.io/gardener-project/cc/github-enterprise-proxy:0.3.0',
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
    not_none(concourse_cfg)

    proxy_url = concourse_cfg.proxy_url()
    host = urlparse(proxy_url).netloc
    tls_secret_name = concourse_cfg.tls_secret_name()

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
                    secret_name=tls_secret_name
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
