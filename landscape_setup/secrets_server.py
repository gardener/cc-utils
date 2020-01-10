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

from ensure import ensure_annotations

from landscape_setup import kube_ctx
from model.secrets_server import (
    SecretsServerConfig,
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
)


@ensure_annotations
def deploy_secrets_server(secrets_server_config: SecretsServerConfig):
    ctx = kube_ctx
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


@ensure_annotations
def generate_secrets_server_service(
    secrets_server_config: SecretsServerConfig,
):
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


@ensure_annotations
def generate_secrets_server_deployment(
    secrets_server_config: SecretsServerConfig,
):
    service_name = secrets_server_config.service_name()
    secret_name = secrets_server_config.secrets().concourse_secret_name()
    # We need to ensure that the labels and selectors match for both the deployment and the service,
    # therefore we base them on the configured service name.
    labels = {'app':service_name}

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
                            image='eu.gcr.io/gardener-project/cc/job-image:latest',
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
                    node_selector={
                        "worker.garden.sapcloud.io/group": "cc-control"
                    },
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
