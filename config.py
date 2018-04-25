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
import requests
import json

from util import urljoin, fail
from util import ctx, CliHints
from model import ConfigFactory, ConfigSetSerialiser as CSS

def serialise_cfg(cfg_dir: CliHints.existing_dir(), cfg_sets: [str], out_file: str):
    factory = ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
    cfg_sets = [factory.cfg_set(cfg_set) for cfg_set in cfg_sets]
    serialiser = CSS(cfg_sets=cfg_sets, cfg_factory=factory)
    with open(out_file, 'w') as f:
        f.write(serialiser.serialise())


class SecretsServerClient(object):
    @staticmethod
    def from_env(
        endpoint_env_var='SECRETS_SERVER_ENDPOINT',
        concourse_secret_env_var='SECRETS_SERVER_CONCOURSE_CFG_NAME',
        cache_file='SECRETS_SERVER_CACHE'
    ):
        if not cache_file in os.environ:
            if not all(map(lambda e: e in os.environ, (endpoint_env_var, concourse_secret_env_var))):
                raise ValueError('the following environment variables must be defined: {v}'.format(
                    v=', '.join((endpoint_env_var, concourse_secret_env_var))
                ))
        cache_file = os.environ.get(cache_file, None)

        return SecretsServerClient(
                endpoint_url=os.environ.get(endpoint_env_var),
                concourse_secret_name=os.environ.get(concourse_secret_env_var),
                cache_file=cache_file
        )

    def __init__(self, endpoint_url, concourse_secret_name, cache_file=None):
        self.url = endpoint_url
        self.concourse_secret_name = concourse_secret_name
        self.cache_file=cache_file

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


def __add_module_command_args(parser):
    parser.add_argument('--server-endpoint', default=None)
    parser.add_argument('--concourse-cfg-name', default=None)
    parser.add_argument('--cache-file', default=None)


def _client():
    args = ctx().args
    try:
        if bool(args.server_endpoint) ^ bool(args.concourse_cfg_name):
            raise ValueError('either all or none of server-endpoint and concourse-cfg-name must be set')
    except AttributeError:
        pass # ignore

    if args.server_endpoint or args.cache_file:
        return SecretsServerClient(
            endpoint_url=args.server_endpoint,
            concourse_secret_name=args.concourse_cfg_name,
            cache_file=args.cache_file
        )
    # fall-back to environemnt variables
    return SecretsServerClient.from_env()


def _parse_model(raw_dict):
    factory = ConfigFactory.from_dict(raw_dict)
    return factory


def _retrieve_model_element(cfg_type: str, cfg_name: str):
    client = _client()
    secrets_dict = client.retrieve_secrets()
    cfg_factory = _parse_model(secrets_dict)

    return cfg_factory._cfg_element(cfg_type_name=cfg_type, cfg_name=cfg_name)


def model_element(cfg_type: str, cfg_name: str, key: str):
    cfg = _retrieve_model_element(cfg_type=cfg_type, cfg_name=cfg_name)

    attrib_path = key.split('.')
    attrib_path.reverse()

    while attrib_path:
        getter = getattr(cfg, attrib_path.pop())
        cfg = getter()

    print(str(cfg))


def attribute(cfg_type: str, cfg_name: str, key: str):
    raw = _retrieve_model_element(cfg_type=cfg_type, cfg_name=cfg_name).raw

    attrib_path = key.split('.')
    attrib_path.reverse()

    while attrib_path:
        attrib = raw.get(attrib_path.pop())

    print(str(attrib))

