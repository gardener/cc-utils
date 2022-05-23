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

import dataclasses
import functools
import os
import typing

import dacite

import ci.util

'''
Execution context. Filled upon invocation of cli.py, read by submodules
'''

args = None # the parsed command line arguments
cfg = None # initialised upon importing this module


@dataclasses.dataclass
class TerminalCfg:
    output_columns: typing.Optional[int] = None
    terminal_type: typing.Optional[str] = None


@dataclasses.dataclass
class GithubRepoMapping:
    repo_url: str
    path: str


@dataclasses.dataclass
class CtxCfg:
    config_dir: typing.Optional[str] = None # points to "root" cfg-repo dir
    github_repo_mappings: tuple[GithubRepoMapping, ...] = ()


@dataclasses.dataclass
class GlobalConfig:
    ctx: typing.Optional[CtxCfg] = None
    terminal: typing.Optional[TerminalCfg] = None


def merge_cfgs(ctor, left, right):
    if not left or not right:
        return left or right # nothing to merge

    left_dict = dataclasses.asdict(left)

    # do not overwrite existing values w/ None
    def none_or_empty(v):
        if v is None or v == () or v == []:
            return True
        return False

    right_dict = {k: v for k,v in dataclasses.asdict(right).items() if not none_or_empty(v)}

    merged = ci.util.merge_dicts(left_dict, right_dict)

    return dacite.from_dict(
        data_class=ctor,
        data=merged,
        config=dacite.Config(cast=[tuple]),
    )


def merge_global_cfg(left: GlobalConfig, right: GlobalConfig):
    merged_cfg = GlobalConfig(
        ctx=merge_cfgs(CtxCfg, left.ctx, right.ctx),
        terminal=merge_cfgs(TerminalCfg, left.terminal, right.terminal),
    )

    return merged_cfg


def _config_from_env():
    env = os.environ

    terminal_config = TerminalCfg(
        output_columns=env.get('COLUMNS'),
        terminal_type=env.get('TERM'),
    )

    if cfg_dir := env.get('CC_CONFIG_DIR'):
        ctx_cfg = CtxCfg(
            config_dir=cfg_dir,
        )
    else:
        ctx_cfg = None

    return GlobalConfig(
        ctx=ctx_cfg,
        terminal=terminal_config,
    )


def _config_from_fs():
    if os.path.isdir('/cc-config'):
        return GlobalConfig(ctx=CtxCfg(config_dir='/cc-config'))

    return None


def _config_from_user_home():
    cfg_file_path = os.path.join(os.path.expanduser('~'), '.cc-utils.cfg')
    if not os.path.isfile(cfg_file_path):
        return None

    raw = ci.util.parse_yaml_file(cfg_file_path) or {}

    return dacite.from_dict(
        data_class=GlobalConfig,
        data=raw,
        config=dacite.Config(cast=[tuple]),
    )


def _config_from_parsed_argv():
    if not args or args.cfg_dir is None:
        return None

    return GlobalConfig(ctx=CtxCfg(config_dir=args.cfg_dir))


def load_config():
    global cfg
    cfg = GlobalConfig()

    additional_cfgs = (
        _config_from_user_home(),
        _config_from_env(),
        _config_from_fs(),
        _config_from_parsed_argv(),
    )

    for additional_cfg in additional_cfgs:
        if not additional_cfg:
            continue

        cfg = merge_global_cfg(cfg, additional_cfg)


load_config()


def _cfg_factory_from_dir():
    if not cfg or not cfg.ctx or not (cfg_dir := cfg.ctx.config_dir):
        return None

    from ci.util import existing_dir
    cfg_dir = existing_dir(cfg_dir)

    from model import ConfigFactory
    factory = ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
    return factory


def _secrets_server_client():
    import ccc.secrets_server
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
    exception = None
    try:
        return ccc.secrets_server.SecretsServerClient.from_env()
    except ValueError as ve:
        exception = ve

    # one last try: use hardcoded default client (will only work if running in
    # CI-cluster)
    try:
        return ccc.secrets_server.SecretsServerClient.default()
    except ValueError:
        pass

    # raise original exception stating missing env-vars
    raise exception


def _cfg_factory_from_secrets_server():
    import model
    raw_dict = _secrets_server_client().retrieve_secrets()
    factory = model.ConfigFactory.from_dict(raw_dict)
    return factory


@functools.lru_cache()
def cfg_factory():
    from ci.util import fail

    factory = _cfg_factory_from_dir()
    # fallback to secrets-server
    if not factory:
        factory = _cfg_factory_from_secrets_server()

    if not factory:
        fail('cfg_factory is required. configure using the global --cfg-dir option or via env')

    return factory


@functools.lru_cache()
def cfg_set(name: str=None):
    if not name:
        if not ci.util._running_on_ci():
            raise RuntimeError('current cfg set only available for "central builds"')
        name = ci.util.current_config_set_name()
    return cfg_factory().cfg_set(name)
