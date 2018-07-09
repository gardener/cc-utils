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
import os
import sys
import json

from urllib.parse import urlparse

from model.base import NamedModelElement, ModelBase, ModelValidationError
from util import (
    ensure_file_exists,
    parse_yaml_file,
    ensure_directory_exists,
    ensure_not_none,
    ensure_not_empty,
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
        cfg_dir = ensure_directory_exists(os.path.abspath(cfg_dir))
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
        raw = ensure_not_none(raw_dict)

        return ConfigFactory(raw_dict=raw)

    def __init__(self, raw_dict: dict):
        self.raw = ensure_not_none(raw_dict)
        if not self.CFG_TYPES in self.raw:
            raise ValueError('missing required attribute: {ct}'.format(ct=self.CFG_TYPES))

    def _configs(self, cfg_name: str):
        return self.raw[cfg_name]

    def _cfg_types(self):
        return {cfg.cfg_type_name(): cfg for cfg in map(ConfigType, self.raw[self.CFG_TYPES].values())}

    def _cfg_types_raw(self):
        return self.raw[self.CFG_TYPES]

    def cfg_set(self, cfg_name: str)->'ConfigurationSet':
        '''
        returns a new `ConfigurationSet` instance for the specified config name backed by the
        configured configuration source.
        '''
        configs_dict = self._configs('cfg_set')

        if not cfg_name in configs_dict:
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

        # retrieve model class c'tor - assume model types are defined in our module
        our_module = sys.modules[__name__]
        element_type = getattr(our_module, cfg_type.cfg_type())
        if not type(element_type) == type:
            raise ValueError()

        # for now, let's assume all of our model element types are subtypes of NamedModelElement
        # (with the exception of ConfigurationSet)
        kwargs = {'raw_dict': self._configs(cfg_type.cfg_type_name())[cfg_name]}

        if element_type == ConfigurationSet:
            kwargs.update({'cfg_name': cfg_name, 'cfg_factory': self})
        else:
            kwargs['name'] = cfg_name

        element_instance = element_type(**kwargs)
        return element_instance

    def _cfg_elements(self, cfg_type_name: str):
        '''Returns an iterable yielding all cfg_elements for a given type known to this ConfigFactory.

        Parameters
        ----------
        cfg_type_name: str
            The name of the cfg_type (as defined in the config-repository) whose instances should be retrieved.

        Yields
        -------
        NamedModelElement
            Instance of the given cfg_type.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        ensure_not_empty(cfg_type_name)

        for element_name in self._cfg_element_names(cfg_type_name):
            yield self._cfg_element(cfg_type_name, element_name)

    def _cfg_element_names(self, cfg_type_name: str):
        '''Returns an iterable containing the names of all cfg-elements for a given cfg_type known to this ConfigFactory.

        Parameters
        ----------
        cfg_type_name: str
            The name of the cfg_type (as defined in the config-repository) whose names should be retrieved.

        Returns
        -------
        Iterable[str]
            Contains the names of all cfg-elements of the given cfg_type known to this ConfigFactory.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        ensure_not_empty(cfg_type_name)

        known_types = self._cfg_types()
        if not cfg_type_name in known_types:
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
            raise AttributeError(name)

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
        self.cfg_sets = ensure_not_none(cfg_sets)
        self.cfg_factory = ensure_not_none(cfg_factory)

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
        serialised_elements['cfg_set'] = {cfg.name() : cfg.raw for cfg in self.cfg_sets}

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
        self.cfg_factory = ensure_not_none(cfg_factory)
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
        '''Returns an iterable yielding all cfg_elements for a given type in this ConfigurationSet.

        Parameters
        ----------
        cfg_type_name: str
            The name of the cfg_type (as defined in the config-repository) whose instances should be retrieved.

        Yields
        -------
        NamedModelElement
            Instance of the given cfg_type.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        ensure_not_empty(cfg_type_name)

        for element_name in self._cfg_element_names(cfg_type_name):
            yield self._cfg_element(cfg_type_name, element_name)

    def _cfg_element_names(self, cfg_type_name: str):
        '''Returns an iterable containing the names of all cfg-elements for a given cfg_type
        in this ConfigurationSet.

        Parameters
        ----------
        cfg_type_name: str
            The name of the cfg_type (as defined in the config-repository) whose names should be retrieved.

        Returns
        -------
        Iterable[str]
            Contains the names of all cfg-elements of the given cfg_type in this ConfigSet.

        Raises
        ------
        ValueError
            If the specified cfg_type is unknown.
        '''
        ensure_not_empty(cfg_type_name)

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
            raise AttributeError(name)
        factory_method = getattr(self.cfg_factory, cfg_type_name)

        if not callable(factory_method):
            raise AttributeError(name)

        def get_default_element(cfg_name=None):
            if not cfg_name:
                cfg_name = self._default_name(cfg_type_name=cfg_type_name)

            return factory_method(cfg_name=cfg_name)
        return get_default_element


class BasicCredentials(ModelBase):
    '''
    Base class for configuration objects that contain basic authentication credentials
    (i.e. a username and a password)

    Not intended to be instantiated
    '''
    def username(self):
        return self.raw.get('username')

    def passwd(self):
        return self.raw.get('password')

    def _required_attributes(self):
        return ['username', 'password']


class GithubConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def ssh_url(self):
        return self.raw.get('sshUrl')

    def http_url(self):
        return self.raw.get('httpUrl')

    def api_url(self):
        return self.raw.get('apiUrl')

    def tls_validation(self):
        return not self.raw.get('disable_tls_validation')

    def webhook_secret(self):
        return self.raw.get('webhook_token')

    def credentials(self):
        return GithubCredentials(self.raw.get('technicalUser'))

    def matches_hostname(self, host_name):
        return host_name.lower() == urlparse(self.http_url()).hostname.lower()

    def _required_attributes(self):
        return ['sshUrl', 'httpUrl', 'apiUrl', 'disable_tls_validation', 'webhook_token', 'technicalUser']

    def _validate_dict(self):
        super()._validate_dict()
        # validation of credentials implicitly happens in the constructor
        self.credentials()


class GithubCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def auth_token(self):
        return self.raw.get('authToken')

    def set_auth_token(self, auth_token):
        self.raw['authToken'] = auth_token

    def private_key(self):
        return self.raw.get('privateKey')

    def email_address(self):
        return self.raw.get('emailAddress')

    def _required_attributes(self):
        required_attribs = set(super()._required_attributes())
        return required_attribs | set(('authToken','privateKey'))


class ContainerRegistryConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def credentials(self):
        # this cfg currently only contains credentials
        return GcrCredentials(self.raw)


class GcrCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def host(self):
        return self.raw.get('host')

    def email(self):
        return self.raw.get('email')


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


class ConcourseConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def all_team_credentials(self):
        return [ConcourseTeamCredentials(team_dict) for team_dict in self.raw.get('teams').values()]

    def external_url(self):
        return self.raw.get('externalUrl')

    def proxy_url(self):
        return self.raw.get('proxyUrl')

    def job_mapping_cfg_name(self):
        return self.raw.get('job_mapping')

    def team_credentials(self, teamname):
        return ConcourseTeamCredentials(self.raw.get('teams').get(teamname))

    def main_team_credentials(self):
        return self.team_credentials('main')

    def helm_chart_default_values_config(self):
        return self.raw.get('helm_chart_default_values_config')

    def helm_chart_values(self):
        return self.raw.get('helm_chart_values', None)

    def image_pull_secret(self):
        return self.raw.get('imagePullSecret')

    def tls_secret_name(self):
        return self.raw.get('tls_secret_name')

    def tls_config(self):
        return self.raw.get('tls_config')

    def deploy_delaying_proxy(self):
        return self.raw.get('deploy_delaying_proxy')

    def kubernetes_cluster_config(self):
        return self.raw.get('kubernetes_cluster_config')

    def disable_github_pr_webhooks(self):
        '''
        If set to True, the rendered concourse pull-request resources don't have webhooks configured.
        This is because of problems using webhooks on our internal Github.
        '''
        return self.raw.get('disable_webhook_for_pr', False)

    def cname_record(self):
        '''
        Returns the CNAME which resolves to the current active Concourse instance.
        '''
        return self.raw.get('cname_record')

    def helm_chart_version(self):
        return self.raw.get('helm_chart_version')

    def _required_attributes(self):
        return ['externalUrl', 'teams', 'helm_chart_default_values_config', 'kubernetes_cluster_config']

    def _validate_dict(self):
        super()._validate_dict()
        # We check for the existence of the 'main'-team as it is the only team that is *required* to
        # exist for any concourse server.
        if not self.raw.get('teams').get('main'):
            raise ModelValidationError('No team "main" defined.')
        if self.deploy_delaying_proxy() and self.proxy_url() is None:
            raise ModelValidationError('Delaying proxy deployment is configured but no proxy-URL is defined.')
        # implicitly validate main team
        self.team_credentials('main')


class ConcourseTeamCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def teamname(self):
        return self.raw.get('teamname')

    def github_auth_team(self, split: bool=False):
        '''
        Returns a string in the form `organisation/team` representing the github-team whose members are able
        to login to this team using github-oauth.

        @param split: if `true` the function will return the organisation and team as a list with two elements,
        i.e. `(organization, team)`
        '''
        if split and self.raw.get('gitAuthTeam'):
            return self.raw.get('gitAuthTeam').split('/')
        return self.raw.get('gitAuthTeam')

    def github_auth_client_id(self):
        return self.raw.get('githubAuthClientId')

    def github_auth_client_secret(self):
        return self.raw.get('githubAuthClientSecret')

    def github_auth_auth_url(self):
        return self.raw.get('githubAuthAuthUrl')

    def github_auth_token_url(self):
        return self.raw.get('githubAuthTokenUrl')

    def github_auth_api_url(self):
        return self.raw.get('githubAuthApiUrl')

    def has_basic_auth_credentials(self):
        if self.raw.get('username') or self.raw.get('password'):
            return True
        return False

    def has_github_oauth_credentials(self):
        if (
            self.raw.get('gitAuthTeam') or
            self.raw.get('githubAuthClientId') or
            self.raw.get('githubAuthClientSecret')
        ):
            return True
        return False

    def has_custom_github_auth_urls(self):
        if (
          self.raw.get('githubAuthAuthUrl') or
          self.raw.get('githubAuthTokenUrl') or
          self.raw.get('githubAuthApiUrl')
        ):
            return True
        return False

    def _required_attributes(self):
        _required_attributes = ['teamname']
        if self.has_basic_auth_credentials():
            _required_attributes.extend(['username', 'password'])
        if self.has_github_oauth_credentials():
            _required_attributes.extend(['gitAuthTeam', 'githubAuthClientId', 'githubAuthClientSecret'])
        if self.has_custom_github_auth_urls():
            _required_attributes.extend(['githubAuthAuthUrl', 'githubAuthTokenUrl', 'githubAuthApiUrl'])
        return _required_attributes

    def _validate_dict(self):
        super()._validate_dict()
        if self.has_github_oauth_credentials():
            github_org_and_team = self.github_auth_team(split=True)
            # explicitly check for expected structure, raise error if not found
            if github_org_and_team and len(github_org_and_team) == 2:
                github_org, github_team = github_org_and_team
                if github_org and github_team:
                    return
            raise ModelValidationError('Invalid github-oauth team. Expected <org>/<team>, got {t}'.format(t=github_org_and_team))


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


class JobMappingSet(NamedModelElement):
    def job_mappings(self):
        return {name: JobMapping(name=name, raw_dict=raw) for name, raw in self.raw.items()}


class JobMapping(NamedModelElement):
    def team_name(self)->str:
        return self.raw.get('concourse_target_team')

    def definition_dirs(self):
        return self.raw['definition_dirs']

    def github_organisations(self):
        return [GithubOrganisationConfig(name, raw) for name, raw in self.raw.get('github_orgs').items()]

    def _required_attributes(self):
        return ['concourse_target_team']


class GithubOrganisationConfig(NamedModelElement):
    def github_cfg_name(self):
        return self.raw.get('github_cfg')

    def org_name(self):
        return self.raw.get('github_org')


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

    def _required_attributes(self):
        return ['private_key', 'certificate']
