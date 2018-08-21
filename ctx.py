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

import enum
import functools
import os

from pathlib import Path

import util

from concourse.pipelines.modelbase import ModelBase
'''
Execution context. Filled upon invocation of cli.py, read by submodules
'''

args=None # the parsed command line arguments


class ConfigBase(ModelBase):

    def __init__(self):
        super().__init__(raw_dict={})

    def _add_config_source(self, config: dict):
        self.raw = util.merge_dicts(self.raw, config)


class ContextConfig(ConfigBase):

    def config_dir(self):
        return self.raw.get('cfg-dir')


class TerminalConfig(ConfigBase):

    def output_columns(self):
        return self.raw.get('output-columns')

    def terminal_type(self):
        return self.raw.get('terminal-type')


class Config(enum.Enum):
    CONTEXT = ContextConfig()
    TERMINAL = TerminalConfig()


def load_config_from_env():
    env = os.environ

    terminal_config = {}
    if 'COLUMNS' in env:
        terminal_config['output-columns'] = env['COLUMNS']
    if 'TERM' in env:
        terminal_config['terminal-type'] = env['TERM']

    context_config = {}
    if 'CC_CONFIG_DIR' in env:
        context_config['cfg-dir'] = env['CC_CONFIG_DIR']

    return {
        'ctx': context_config,
        'terminal': terminal_config,
    }


def load_config_from_user_home():
    config_file = Path.home() / '.cc-utils.cfg'
    if config_file.is_file():
        return util.parse_yaml_file(config_file)
    return {}


def add_config_source(config_source: dict):
    if config_source.get('ctx') is not None:
        Config.CONTEXT.value._add_config_source(
            config=config_source.get('ctx')
        )
    if config_source.get('terminal') is not None:
        Config.TERMINAL.value._add_config_source(
            config=config_source.get('terminal')
        )


def load_config():
    home_config = load_config_from_user_home()
    env_config = load_config_from_env()
    merged = util.merge_dicts(home_config, env_config)
    add_config_source(merged)


load_config()


def load_config_from_args():
    context_config = {}
    if args.cfg_dir is not None:
        context_config['cfg-dir'] = args.cfg_dir

    return {
        'ctx': context_config,
    }


def _cfg_factory_from_dir():
    if Config.CONTEXT.value.config_dir() is None:
        return None

    from util import existing_dir
    cfg_dir = existing_dir(Config.CONTEXT.value.config_dir())

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
