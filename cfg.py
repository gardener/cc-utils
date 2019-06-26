# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

import ccc.secrets_server
from util import ctx


def _client():
    args = ctx().args
    try:
        if bool(args.server_endpoint) ^ bool(args.concourse_cfg_name):
            raise ValueError(
                    'either all or none of server-endpoint and concourse-cfg-name must be set'
            )
        if args.server_endpoint or args.cache_file:
            return ccc.secrets_server.SecretsServerClient(
                endpoint_url=args.server_endpoint,
                concourse_secret_name=args.concourse_cfg_name,
                cache_file=args.cache_file
            )
    except AttributeError:
        pass # ignore

    # fall-back to environment variables
    return ccc.secrets_server.SecretsServerClient.from_env()


def _parse_model(raw_dict):
    from model import ConfigFactory
    factory = ConfigFactory.from_dict(raw_dict)
    return factory


def _retrieve_model_element(cfg_type: str, cfg_name: str):
    cfg_factory = ctx().cfg_factory()
    return cfg_factory._cfg_element(cfg_type_name=cfg_type, cfg_name=cfg_name)
