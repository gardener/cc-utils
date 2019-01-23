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
import kube.ctx
from ensure import ensure_annotations

from util import (
    not_none,
    not_empty,
    fail,
    which,
)
from model.tls import (
    TlsConfig,
)
from model.kubernetes import (
    KubernetesConfig,
)

kube_ctx = kube.ctx.Ctx()


def get_cluster_version_info():
    api = kube_ctx.create_version_api()
    return api.get_code()


def ensure_cluster_version(kubernetes_config: KubernetesConfig):
    not_none(kubernetes_config)

    cluster_version_info = get_cluster_version_info()
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
    helm_executable = which('helm')
    with open(os.devnull) as devnull:
        subprocess.run([helm_executable, 'repo', 'update'], check=True, stdout=devnull)
    return helm_executable


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

    ctx = kube_ctx
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
