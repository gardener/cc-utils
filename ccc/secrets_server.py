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

import json
import os
import socket

import requests

from ci.util import urljoin


class SecretsServerClient:
    @staticmethod
    def from_env(
        endpoint_env_var='SECRETS_SERVER_ENDPOINT',
        concourse_secret_env_var='SECRETS_SERVER_CONCOURSE_CFG_NAME',
        cache_file='SECRETS_SERVER_CACHE'
    ):
        if cache_file not in os.environ:
            if not all(map(
                        lambda e: e in os.environ,
                        (endpoint_env_var, concourse_secret_env_var)
                        )):
                raise ValueError('the following environment variables must be defined: {v}'.format(
                    v=', '.join((endpoint_env_var, concourse_secret_env_var))
                ))
        cache_file = os.environ.get(cache_file, None)

        return SecretsServerClient(
                endpoint_url=os.environ.get(endpoint_env_var),
                concourse_secret_name=os.environ.get(concourse_secret_env_var),
                cache_file=cache_file
        )

    @staticmethod
    def default():
        # hardcode default endpoint name (usually injected via env (see above))
        default_secrets_server_hostname = 'secrets-server.concourse.svc.cluster.local'
        try:
            socket.getaddrinfo(default_secrets_server_hostname, 80)
        except socket.gaierror:
            raise ValueError('secrets-server not accessible (are you running in ci-cluster?)')
        # also hardcode default url path (usually injected via env)
        default_secrets_path = 'concourse-secrets/concourse_cfg'

        return SecretsServerClient(
            endpoint_url=f'http://{default_secrets_server_hostname}',
            concourse_secret_name=default_secrets_path,
            cache_file=None,
        )

    def __init__(self, endpoint_url, concourse_secret_name, cache_file=None):
        self.url = endpoint_url
        self.concourse_secret_name = concourse_secret_name
        self.cache_file = cache_file

    def retrieve_secrets(self):
        if self.cache_file and os.path.isfile(self.cache_file):
            with open(self.cache_file) as f:
                return json.load(f)

        request_url = urljoin(self.url, self.concourse_secret_name)
        response = requests.get(request_url)
        # pylint: disable=no-member
        if not response.status_code == requests.codes.ok:
        # pylint: enable=no-member
            raise RuntimeError('secrets_server sent {d}: {m}'.format(
                d=response.status_code,
                m=response.content
            ))

        if self.cache_file:
            with open(self.cache_file, 'w') as f:
                json.dump(response.json(), f)

        return response.json()
