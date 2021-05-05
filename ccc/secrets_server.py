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

import base64
import json
import os
import socket
import typing

import Crypto.Util.Padding
import requests

from ci.util import urljoin
import model.secret


def get_secret_cfg_from_env_if_available(
    key_env_var='SECRET_KEY',
    cipher_algorithm='SECRET_CIPHER_ALGORITHM',
) -> typing.Optional[model.secret.SecretData]:
    if key_env_var in os.environ and cipher_algorithm in os.environ:
        secret_key = base64.b64decode(os.environ.get(key_env_var).encode('utf-8'))
        cipher_algorithm = model.secret.Cipher(os.environ.get(cipher_algorithm))

        secret = model.secret.SecretData(key=secret_key, cipher_algorithm=cipher_algorithm)

        return secret


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

        secret = get_secret_cfg_from_env_if_available()

        return SecretsServerClient(
            endpoint_url=os.environ.get(endpoint_env_var),
            concourse_secret_name=os.environ.get(concourse_secret_env_var),
            cache_file=cache_file,
            secret=secret,
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

        # secret will be set when env vars are set
        secret = get_secret_cfg_from_env_if_available()
        if secret:
            # if secret env vars are set we want to use encryption
            default_secrets_path = 'encrypted-concourse-secrets/encrypted_concourse_cfg'
        else:
            default_secrets_path = 'concourse-secrets/concourse_cfg'

        return SecretsServerClient(
            endpoint_url=f'http://{default_secrets_server_hostname}',
            concourse_secret_name=default_secrets_path,
            cache_file=None,
            secret=secret,
        )

    def __init__(
        self,
        endpoint_url,
        concourse_secret_name,
        cache_file=None,
        secret: model.secret.SecretData = None,
    ):
        self.url = endpoint_url
        self.concourse_secret_name = concourse_secret_name
        self.cache_file = cache_file
        self.secret = secret

    def retrieve_secrets(self):
        if self.cache_file and os.path.isfile(self.cache_file):
            with open(self.cache_file, 'rb') as f:
                if self.secret:
                    raw_data = _decrypt_cipher_text(
                        encrypted_cipher_text=f.read(),
                        secret=self.secret,
                    )
                    return json.loads(raw_data)
                else:
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
            with open(self.cache_file, 'wb') as f:
                if self.secret:
                    f.write(response.content)
                else:
                    json.dump(response.json(), f)

        if self.secret:
            raw_data = _decrypt_cipher_text(
                encrypted_cipher_text=response.content,
                secret=self.secret,
            )
            return json.loads(raw_data)
        else:
            return response.json()


def _decrypt_cipher_text(encrypted_cipher_text: bytes, secret: model.secret.SecretData):
    from Crypto.Cipher import AES

    if not (cipher_alg := secret.cipher_algorithm) is model.secret.Cipher.AES_ECB:
        raise NotImplementedError(cipher_alg)

    cipher = AES.new(key=secret.key, mode=AES.MODE_ECB)

    decrypted_cipher = cipher.decrypt(encrypted_cipher_text)
    return Crypto.Util.Padding.unpad(
        padded_data=decrypted_cipher,
        block_size=AES.block_size,
    )
