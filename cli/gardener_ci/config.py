# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

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


def serialise_cfg(cfg_dir: CliHints.existing_dir(), cfg_sets: [str], out_file: str):
    factory = ConfigFactory.from_cfg_dir(cfg_dir=cfg_dir)
    cfg_sets = [factory.cfg_set(cfg_set) for cfg_set in cfg_sets]
    serialiser = CSS(cfg_sets=cfg_sets, cfg_factory=factory)
    with open(out_file, 'w') as f:
        f.write(serialiser.serialise())


def attribute(cfg_type: str, cfg_name: str, key: str, json: bool=False):
    raw = _retrieve_model_element(cfg_type=cfg_type, cfg_name=cfg_name).raw

    attrib_path = key.split('.')
    attrib_path.reverse()

    while attrib_path:
        attrib = raw.get(attrib_path.pop())
        raw = attrib

    if json:
        print(json_m.dumps(attrib))
    else:
        print(str(attrib))


def model_element(cfg_type: str, cfg_name: str, key: str):
    cfg = _retrieve_model_element(cfg_type=cfg_type, cfg_name=cfg_name)

    attrib_path = key.split('.')
    attrib_path.reverse()

    while attrib_path:
        getter = getattr(cfg, attrib_path.pop())
        cfg = getter()

    print(str(cfg))


def __add_module_command_args(parser):
    parser.add_argument('--server-endpoint', default=None)
    parser.add_argument('--concourse-cfg-name', default=None)
    parser.add_argument('--cache-file', default=None)
