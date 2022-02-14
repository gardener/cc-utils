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

import pathlib
import json as json_m
import yaml

from ci.util import CliHints, ctx,existing_dir
from model import ConfigFactory, ConfigSetSerialiser as CSS


def _retrieve_model_element(cfg_type: str, cfg_name: str):
    cfg_factory = ctx().cfg_factory()
    return cfg_factory._cfg_element(cfg_type_name=cfg_type, cfg_name=cfg_name)


def export_kubeconfig(
    kubernetes_config_name: str,
    output_file: str,
):
    '''Write the kubeconfig contained in a kubernetes config to a given path.
    '''
    cfg_factory = ctx().cfg_factory()
    kubernetes_cfg = cfg_factory.kubernetes(kubernetes_config_name)

    destination_path = pathlib.Path(output_file).resolve()
    existing_dir(destination_path.parent)

    with destination_path.open(mode='w') as f:
        yaml.dump(kubernetes_cfg.kubeconfig(), f)


def serialise_cfg(cfg_dir: CliHints.existing_dir(), out_file: str, cfg_sets: [str] = []):
    factory = ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
    if not cfg_sets:
        cfg_sets = factory._cfg_element_names('cfg_set')
    cfg_sets = [factory.cfg_set(cfg_set) for cfg_set in cfg_sets]
    serialiser = CSS(cfg_sets=cfg_sets, cfg_factory=factory)
    with open(out_file, 'w') as f:
        f.write(serialiser.serialise())


def attribute(
    cfg_type: str,
    cfg_name: str,
    key: str,
    output_file: str = None,
    json: bool=False,
):
    raw = _retrieve_model_element(cfg_type=cfg_type, cfg_name=cfg_name).raw

    attrib_path = key.split('.')
    attrib_path.reverse()

    while attrib_path:
        attrib = raw.get(attrib_path.pop())
        raw = attrib

    output = json_m.dumps(attrib) if json else str(attrib)

    if output_file:
        with open(output_file, 'w') as f:
            f.write(output)
    else:
        print(output)


def model_element(
    cfg_type: str,
    cfg_name: str,
    key: str,
    output_file: str = None,
):
    cfg = _retrieve_model_element(cfg_type=cfg_type, cfg_name=cfg_name)

    attrib_path = key.split('.')
    attrib_path.reverse()

    while attrib_path:
        getter = getattr(cfg, attrib_path.pop())
        cfg = getter()

    if output_file:
        with open(output_file, 'w') as f:
            f.write(str(cfg))
    else:
        print(str(cfg))


def __add_module_command_args(parser):
    parser.add_argument('--server-endpoint', default=None)
    parser.add_argument('--concourse-cfg-name', default=None)
    parser.add_argument('--cache-file', default=None)
