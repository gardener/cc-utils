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

import functools

'''
Execution context. Filled upon invocation of cli.py, read by submodules
'''

args=None # the parsed command line arguments

def _cfg_factory_from_dir():
    # XXX: args does always have a cfg_dir attribute, but pylint does not always understand this
    if not args or not hasattr(args, 'cfg_dir') or not getattr(args, 'cfg_dir'):
        return None

    from util import existing_dir
    cfg_dir = existing_dir(getattr(args, 'cfg_dir'))

    from model import ConfigFactory
    factory = ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
    return factory


def _cfg_factory_from_secrets_server():
    import config
    return config._parse_model(config._client().retrieve_secrets())


@functools.lru_cache()
def cfg_factory():
    from util import fail

    factory = _cfg_factory_from_dir()
    # fallback to secrets-server
    if not factory:
        factory = _cfg_factory_from_secrets_server()

    if not factory:
        fail('cfg_factory is required. configure using the global --cfg-dir option or via env')

    return factory
