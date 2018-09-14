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

import functools
import os
import sys
import json
import pkgutil

from model.base import (
    BasicCredentials,
    ConfigElementNotFoundError,
    ModelBase,
    NamedModelElement,
)
from util import (
    parse_yaml_file,
    existing_dir,
    not_none,
    not_empty,
    check_type,
)

'''
Configuration model and retrieval handling.

Users of this module will most likely want to create an instance of `ConfigFactory` and use it
to create `ConfigurationSet` instances.

Configuration sets are factories themselves, that are backed with a configuration source.
They create concrete configuration instances. While technically modifiable, all configuration
instances should not be altered by users. Configuration objects should usually not be
instantiated by users of this module.
'''


class ConfigFactory(object):
    '''Creates configuration model element instances from the underlying configuration source

    Configuration elements are organised in a two-level hierarchy: Configuration type
    (named cfg_type by convention) which specifies a configuration schema (and semantics) and
    Configuration element name (named cfg_name or element_name by convention).

    Configuration model elements may be retrieved through one of two methods:

        - via the generic `_cfg_element(cfg_type_name, cfg_name)`
        - via a "factory method" (if defined in cfg_type) - example: `github(cfg_name)`

    There is a special configuration type named `ConfigurationSet`, which is used to group
    sets of configuration elements. Configuration sets expose an API equivalent to ConfigFactory.
    '''

    CFG_TYPES = 'cfg_types'

    @staticmethod
    def from_cfg_dir(cfg_dir: str, cfg_types_file='config_types.yaml'):
        cfg_dir = existing_dir(os.path.abspath(cfg_dir))
        cfg_types_dict = parse_yaml_file(os.path.join(cfg_dir, cfg_types_file))
        raw = {}

        raw[ConfigFactory.CFG_TYPES] = cfg_types_dict

        def parse_cfg(cfg_type):
            # assume for now that there is exactly one cfg source (file)
            cfg_sources = list(cfg_type.sources())
            if not len(cfg_sources) == 1:
                raise ValueError('currently, only exactly one cfg file is supported per type')

            cfg_file = cfg_sources[0].file()
            parsed_cfg =  parse_yaml_file(os.path.join(cfg_dir, cfg_file))
            return parsed_cfg

        # parse all configurations
        for cfg_type in map(ConfigType, cfg_types_dict.values()):
            cfg_name = cfg_type.cfg_type_name()
            raw[cfg_name] = parse_cfg(cfg_type)

        return ConfigFactory(raw_dict=raw)

    @staticmethod
    def from_dict(raw_dict: dict):
        raw = not_none(raw_dict)

        return ConfigFactory(raw_dict=raw)

    def __init__(self, raw_dict: dict):
        self.raw = not_none(raw_dict)
        if self.CFG_TYPES not in self.raw:
            raise ValueError('missing required attribute: {ct}'.format(ct=self.CFG_TYPES))

    def _configs(self, cfg_name: str):
        return self.raw[cfg_name]

    def _cfg_types(self):
        return {
            cfg.cfg_type_name(): cfg for
            cfg in map(ConfigType, self.raw[self.CFG_TYPES].values())
        }

    def _cfg_types_raw(self):
        return self.raw[self.CFG_TYPES]

    def cfg_set(self, cfg_name: str)->'ConfigurationSet':
        '''
        returns a new `ConfigurationSet` instance for the specified config name backed by the
        configured configuration source.
        '''
        configs_dict = self._configs('cfg_set')

        if cfg_name not in configs_dict:
            raise ValueError('no cfg named {c} in {cs}'.format(
                c=cfg_name,
                cs=', '.join(configs_dict.keys())
            )
            )
        return ConfigurationSet(
            cfg_factory=self,
            cfg_name=cfg_name,
            raw_dict=configs_dict[cfg_name]
        )

    def _cfg_element(self, cfg_type_name: str, cfg_name: str):
        cfg_type = self._cfg_types().get(cfg_type_name, None)
        if not cfg_type:
            raise ValueError('unknown cfg_type: ' + str(cfg_type_name))

        # retrieve model class c'tor - search module and sub-modules
        # TODO: switch to fully-qualified type names
        own_module = sys.modules[__name__]
        submodule_names = [
            own_module.__name__ + '.' + m.name
            for m in pkgutil.iter_modules(own_module.__path__)
        ]
        for module_name in [__name__] + submodule_names:
            module = sys.modules[module_name]
            # skip if module does not define our type
            if not hasattr(module, cfg_type.cfg_type()):
                continue

            # if type is defined, validate
            element_type = getattr(module, cfg_type.cfg_type())
            if not type(element_type) == type:
                raise ValueError()
            # found it
            break
        else:
            raise ValueError('failed to find cfg type: ' + str(cfg_type.cfg_type()))

        # for now, let's assume all of our model element types are subtypes of NamedModelElement
        # (with the exception of ConfigurationSet)
        configs = self._configs(cfg_type.cfg_type_name())
        if cfg_name not in configs:
            raise ConfigElementNotFoundError('no such cfg element: {cn}. Known: {es}'.format(
                cn=cfg_name,
                es=', '.join(configs.keys())
            )
            )
        kwargs = {'raw_dict': configs[cfg_name]}

        if element_type == ConfigurationSet:
            kwargs.update({'cfg_name': cfg_name, 'cfg_factory': self})
        else:
            kwargs['name'] = cfg_name

        element_instance = element_type(**kwargs)
        return element_instance

    def _cfg_elements(self, cfg_type_name: str):
        '''Returns all cfg_elements for the given cfg_type.

        Parameters
        ----------
        cfg_type_name: str
            The name of the cfg_type whose instances should be retrieved.

        Yields
        -------
        NamedModelElement
            Instance of the given cfg_type.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)

        for element_name in self._cfg_element_names(cfg_type_name):
            yield self._cfg_element(cfg_type_name, element_name)

    def _cfg_element_names(self, cfg_type_name: str):
        '''Returns cfg-elements of the given cfg_type

        Parameters
        ----------
        cfg_type_name: str
            The cfg type name

        Returns
        -------
        Iterable[str]
            Contains the names of all cfg-elements of the given cfg_type known to this ConfigFactory.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)

        known_types = self._cfg_types()
        if cfg_type_name not in known_types:
            raise ValueError("Unknown config type '{c}'. Known types: {k}".format(
                c = cfg_type_name,
                k = ', '.join(known_types.keys()),
            ))
        if cfg_type_name in self.raw:
            return set(self.raw[cfg_type_name].keys())
        else:
            return set()

    def __getattr__(self, cfg_type_name):
        for cfg_type in self._cfg_types().values():
            if cfg_type.factory_method() == cfg_type_name:
                break
        else:
            raise AttributeError(cfg_type_name)

        return functools.partial(self._cfg_element, cfg_type_name)


class ConfigType(ModelBase):
    '''
    represents a configuration type (used for serialisation and deserialisation)
    '''

    def sources(self):
        return map(ConfigTypeSource, self.raw.get('src'))

    def factory_method(self):
        return self.raw.get('model').get('factory_method')

    def cfg_type_name(self):
        return self.raw.get('model').get('cfg_type_name')

    def cfg_type(self):
        return self.raw.get('model').get('type')


class ConfigTypeSource(ModelBase):
    def file(self):
        return self.raw.get('file')


class ConfigSetSerialiser(object):
    def __init__(self, cfg_sets: 'ConfigurationSet', cfg_factory: ConfigFactory):
        self.cfg_sets = not_none(cfg_sets)
        self.cfg_factory = not_none(cfg_factory)

    def serialise(self, output_format='json'):
        if not output_format == 'json':
            raise ValueError('not implemented')
        if len(self.cfg_sets) < 1:
            return '{}' # early exit for empty cfg_sets-set

        cfg_types = self.cfg_factory._cfg_types()
        # collect all cfg_names (<cfg-type>:[cfg-name])
        cfg_mappings = {}
        for cfg_mapping in [cfg_set._cfg_mappings() for cfg_set in self.cfg_sets]:
            for cfg_type_name, cfg_set_mapping in cfg_mapping:
                cfg_type = cfg_types[cfg_type_name]
                if cfg_type not in cfg_mappings:
                    cfg_mappings[cfg_type] = set()
                cfg_mappings[cfg_type].update(cfg_set_mapping['config_names'])

        # assumption: all cfg_sets share the same cfg_factory / all cfg_names are organized in one
        # global, flat namespace
        def serialise_element(cfg_type, cfg_names):
            elem_cfgs = {}

            for cfg_name in cfg_names:
                element = self.cfg_factory._cfg_element(cfg_type.cfg_type_name(), cfg_name)
                elem_cfgs[element.name()] = element.raw

            return (cfg_type.cfg_type_name(), elem_cfgs)

        serialised_elements = dict([serialise_element(t, n) for t, n in cfg_mappings.items()])

        # store cfg_set
        serialised_elements['cfg_set'] = {cfg.name(): cfg.raw for cfg in self.cfg_sets}

        # store cfg_types metadata (TODO: patch source attributes)
        serialised_elements[ConfigFactory.CFG_TYPES] = self.cfg_factory._cfg_types_raw()

        return json.dumps(serialised_elements, indent=2)


class ConfigurationSet(NamedModelElement):
    '''
    Represents a set of corresponding configuration. Instances are created by `ConfigFactory`.
    `ConfigurationSet` is itself a factory, offering a set of factory methods which create
    concrete configuration objects based on the backing configuration source.

    Not intended to be instantiated by users of this module
    '''

    def __init__(self, cfg_factory, cfg_name, *args, **kwargs):
        self.cfg_factory = not_none(cfg_factory)
        super().__init__(name=cfg_name, *args, **kwargs)

        # normalise cfg mappings
        for cfg_type_name, entry in self.raw.items():
            if type(entry) == dict:
                entry = {
                    'config_names': entry['config_names'],
                    'default': entry.get('default', None)
                }
            elif type(entry) == str:
                entry = {'config_names': [entry], 'default': entry}

            self.raw[cfg_type_name] = entry

    def _cfg_mappings(self):
        return self.raw.items()

    def _cfg_element(self, cfg_type_name: str, cfg_name=None):
        if not cfg_name:
            cfg_name = self.raw[cfg_type_name]['default']

        return self.cfg_factory._cfg_element(
            cfg_type_name=cfg_type_name,
            cfg_name=cfg_name,
        )

    def _cfg_elements(self, cfg_type_name: str):
        '''Returns all container cfg elements of the given cfg_type

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)

        for element_name in self._cfg_element_names(cfg_type_name):
            yield self._cfg_element(cfg_type_name, element_name)

    def _cfg_element_names(self, cfg_type_name: str):
        '''Returns all container cfg element names

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        not_empty(cfg_type_name)

        # ask factory for all known names. This ensures that the existance of the type is checked.
        all_cfg_element_names = self.cfg_factory._cfg_element_names(cfg_type_name=cfg_type_name)

        if cfg_type_name in self.raw.keys():
            return all_cfg_element_names & set(self.raw[cfg_type_name]['config_names'])
        else:
            return set()

    def _default_name(self, cfg_type_name, cfg_name=None):
        if not cfg_name:
            return self.raw[cfg_type_name]['default']
        else:
            return cfg_name

    def __getattr__(self, cfg_type_name):
        if not hasattr(self.cfg_factory, cfg_type_name):
            raise AttributeError(cfg_type_name)
        factory_method = getattr(self.cfg_factory, cfg_type_name)

        if not callable(factory_method):
            raise AttributeError(cfg_type_name)

        def get_default_element(cfg_name=None):
            if not cfg_name:
                cfg_name = self._default_name(cfg_type_name=cfg_type_name)

            return factory_method(cfg_name=cfg_name)
        return get_default_element


class ProtecodeConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def credentials(self):
        return ProtecodeCredentials(self.raw.get('credentials'))

    def api_url(self):
        return self.raw.get('api_url')

    def tls_verify(self):
        return self.raw.get('tls_verify', True)


class ProtecodeCredentials(BasicCredentials):
    pass


class AwsProfile(NamedModelElement):
    def region(self):
        return self.raw.get('region')

    def access_key_id(self):
        return self.raw.get('aws_access_key_id')

    def secret_access_key(self):
        return self.raw.get('aws_secret_access_key')

    def _required_attributes(self):
        return ['region','access_key_id','secret_access_key']


class EmailConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def smtp_host(self):
        return self.raw.get('host')

    def smtp_port(self):
        return self.raw.get('port')

    def use_tls(self):
        return self.raw.get('use_tls')

    def sender_name(self):
        return self.raw.get('sender_name')

    def credentials(self):
        return EmailCredentials(self.raw.get('credentials'))

    def _required_attributes(self):
        return ['host', 'port', 'credentials']

    def _validate_dict(self):
        super()._validate_dict()
        # ensure credentials are valid - validation implicitly happens in the constructor.
        self.credentials()


class EmailCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    pass


class KubernetesConfig(NamedModelElement):
    def kubeconfig(self):
        return self.raw.get('kubeconfig')

    def cluster_version(self):
        return self.raw.get('version')


class SecretsServerConfig(NamedModelElement):
    def namespace(self):
        return self.raw.get('namespace')

    def service_name(self):
        return self.raw.get('service_name')

    def endpoint_url(self):
        return 'http://{sn}.{ns}.svc.cluster.local'.format(
            sn=self.service_name(),
            ns=self.namespace(),
        )

    def secrets(self):
        return SecretsServerSecrets(raw_dict=self.raw['secrets'])


class SecretsServerSecrets(ModelBase):
    def concourse_secret_name(self):
        return self.raw.get('concourse_config').get('name')

    def concourse_attribute(self):
        return self.raw.get('concourse_config').get('attribute')

    def cfg_set_names(self):
        return self.raw['cfg_sets']


class TlsConfig(NamedModelElement):
    def private_key(self):
        return self.raw.get('private_key')

    def certificate(self):
        return self.raw.get('certificate')

    def set_private_key(self, private_key: str):
        self.raw['private_key'] = private_key

    def set_certificate(self, certificate: str):
        self.raw['certificate'] = certificate

    def _required_attributes(self):
        return ['private_key', 'certificate']


class SlackConfig(NamedModelElement):
    def api_token(self):
        return self.raw.get('api_token')

    def _required_attributes(self):
        return ['api_token']
