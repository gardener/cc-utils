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
import ensure
import json
import logging
import os
import socket
import typing

from Crypto.Cipher import AES
import Crypto.Util.Padding
import requests

import ci.log
from ci.util import urljoin
import model
import model.concourse
import model.secret
import model.secrets_server


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


def get_secret_cfg_from_env_if_available(
    key_env_var='SECRET_KEY',
    cipher_algorithm_env_var='SECRET_CIPHER_ALGORITHM',
) -> typing.Optional[model.secret.SecretData]:
    if not all((name in os.environ for name in (key_env_var, cipher_algorithm_env_var))):
        return None

    secret_key = base64.b64decode(os.environ.get(key_env_var).encode('utf-8'))
    cipher_algorithm = model.secret.Cipher(os.environ.get(cipher_algorithm_env_var))
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

        # secret will be read from environemt variables
        secret = get_secret_cfg_from_env_if_available()
        if current_team := os.environ.get('CONCOURSE_CURRENT_TEAM'):
            default_secrets_path = model.secrets_server._org_based_secret_url_path(
                target_secret_name=model.concourse.cfg_name_from_team(current_team),
                secret_cfg_name=model.concourse.secret_cfg_name_for_team(current_team),
            )
        else:
            logger.warning(
                'CONCOURSE_CURRENT_TEAM not found in environment. Needed for secret retrieval.',
            )

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
    if (cipher_alg := secret.cipher_algorithm) is model.secret.Cipher.PLAINTEXT:
        return encrypted_cipher_text
    elif cipher_alg is model.secret.Cipher.AES_ECB:
        pass
    else:
        raise NotImplementedError(cipher_alg)

    from Crypto.Cipher import AES

    cipher = AES.new(key=secret.key, mode=AES.MODE_ECB)

    decrypted_cipher = cipher.decrypt(encrypted_cipher_text)
    try:
        unpadded_cipher = Crypto.Util.Padding.unpad(
            padded_data=decrypted_cipher,
            block_size=AES.block_size,
        )
    except ValueError as ve:
        raise ValueError("Unable to decrypt secret. Key doesn't fit cipher text.") from ve

    return unpadded_cipher


@ensure.ensure_annotations
def encrypt_data(
    key: bytes,
    cipher_algorithm: str,
    serialized_secret_data: bytes,
) -> bytes:
    cipher_algorithm = model.secret.Cipher(cipher_algorithm)

    if cipher_algorithm is model.secret.Cipher.PLAINTEXT:
        return serialized_secret_data
    elif cipher_algorithm is model.secret.Cipher.AES_ECB:
        secret_key = base64.b64decode(key)
        cipher = AES.new(secret_key, AES.MODE_ECB)
        cipher_text = cipher.encrypt(
            Crypto.Util.Padding.pad(
                data_to_pad=serialized_secret_data,
                block_size=AES.block_size,
            )
        )
    else:
        logger.error(f'cipher algorithm defined is not supported. {cipher_algorithm}')
        raise NotImplementedError(cipher_algorithm)

    return cipher_text
