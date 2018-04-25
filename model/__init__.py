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
import sys
import json

from urllib.parse import urlparse

from model.base import NamedModelElement, ModelBase, ModelValidationError
from util import ensure_file_exists, parse_yaml_file, ensure_directory_exists, ensure_not_none

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
    '''
    Creates `ConfigurationSet` instances from a configuration collection.

    Currently, the only example for configuration collections is the contents of
    the private github repository 'kubernetes/cc-config', whose
    root directory is accepted as an input.

    The returned `ConfigurationSet` instances could in turn also be regarded as
    factories, as they create concrete configuration model instances from configuration
    data contained in the given configuration directory.
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
            parsed_cfg =  parse_yaml_file(os.path.join(cfg_dir, cfg_file), as_snd=False)
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

    def concourse(self, cfg_name):
        return self._cfg_element(cfg_type_name='concourse', cfg_name=cfg_name)

    def container_registry(self, cfg_name):
        return self._cfg_element(cfg_type_name='container_registry', cfg_name=cfg_name)

    def email(self, cfg_name):
        return self._cfg_element(cfg_type_name='email', cfg_name=cfg_name)

    def github(self, cfg_name):
        return self._cfg_element(cfg_type_name='github', cfg_name=cfg_name)

    def job_mapping(self, cfg_name):
        return self._cfg_element(cfg_type_name='job_mapping', cfg_name=cfg_name)

    def kubernetes(self, cfg_name):
        return self._cfg_element(cfg_type_name='kubernetes', cfg_name=cfg_name)

    def secrets_server(self, cfg_name):
        return self._cfg_element(cfg_type_name='secrets_server', cfg_name=cfg_name)


class ConfigType(ModelBase):
    '''
    represents a configuration type (used for serialisation and deserialisation)
    '''
    def sources(self):
        return map(ConfigTypeSource, self.snd.src)

    def factory_method(self):
        return self.snd.model.factory_method

    def cfg_type_name(self):
        return self.snd.model.cfg_type_name

    def cfg_type(self):
        return self.snd.model.type


class ConfigTypeSource(ModelBase):
    def file(self):
        return self.snd.file


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

        # assumption: all cfg_sets share the same cfg_factory / all cfg_names are organised in one
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

    def _default_name(self, cfg_type_name, cfg_name=None):
        if not cfg_name:
            return self.raw[cfg_type_name]['default']
        else:
            return cfg_name

    def email(self, cfg_name=None):
        return self.cfg_factory.email(self._default_name('email', cfg_name))

    def concourse(self, cfg_name=None):
        return self.cfg_factory.concourse(self._default_name('concourse', cfg_name))

    def github(self, cfg_name=None):
        return self.cfg_factory.github(self._default_name('github', cfg_name))

    def container_registry(self, cfg_name=None):
        return self.cfg_factory.container_registry(self._default_name('container_registry', cfg_name))

    def job_mapping(self, cfg_name=None):
        return self.cfg_factory.job_mapping(self._default_name('job_mapping', cfg_name))

    def kubernetes(self, cfg_name=None):
        return self.cfg_factory.kubernetes(self._default_name('kubernetes', cfg_name))

    def secrets_server(self, cfg_name=None):
        return self.cfg_factory.secrets_server(self._default_name('secrets_server', cfg_name))


class BasicCredentials(ModelBase):
    '''
    Base class for configuration objects that contain basic authentication credentials
    (i.e. a username and a password)

    Not intended to be instantiated
    '''
    def username(self):
        return self.snd.username

    def passwd(self):
        return self.snd.password

    def _required_attributes(self):
        return ['username', 'password']


class GithubConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def ssh_url(self):
        return self.snd.sshUrl

    def http_url(self):
        return self.snd.httpUrl

    def api_url(self):
        return self.snd.apiUrl

    def tls_validation(self):
        return not self.snd.disable_tls_validation

    def webhook_secret(self):
        return self.snd.webhook_user.authToken

    def credentials(self):
        return GithubCredentials(self.snd.technicalUser)

    def _required_attributes(self):
        return ['sshUrl', 'httpUrl', 'apiUrl', 'disable_tls_validation', 'webhook_token', 'webhook_user', 'technicalUser']

    def _validate_dict(self):
        super()._validate_dict()
        if not self.snd.webhook_user.authToken:
            raise ModelValidationError('Missing required github-config attribute: webhook_user.authToken')
        # validation of credentials implicitly happens in the constructor
        self.credentials()


class GithubCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def auth_token(self):
        return self.snd.authToken

    def private_key(self):
        return self.snd.privateKey

    def email_address(self):
        return self.snd.emailAddress

    def _required_attributes(self):
        required_attribs = set(super()._required_attributes())
        return required_attribs | set(('authToken','privateKey'))


class ContainerRegistryConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def credentials(self):
        # this cfg currently only contains credentials
        return GcrCredentials(self.snd)


class GcrCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def host(self):
        return self.snd.host

    def email(self):
        return self.snd.email


class ConcourseConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def all_team_credentials(self):
        return [ConcourseTeamCredentials(team_dict) for team_dict in self.snd.teams.values()]

    def external_url(self):
        return self.snd.externalUrl

    def proxy_url(self):
        if self.snd.proxyUrl:
            return self.snd.proxyUrl
        return self.external_url()

    def job_mapping_cfg_name(self):
        return self.snd.job_mapping

    def team_credentials(self, teamname):
        return ConcourseTeamCredentials(self.snd.teams[teamname])

    def main_team_credentials(self):
        return self.team_credentials('main')

    def helm_chart_default_values_config(self):
        return self.snd.helm_chart_default_values_config

    def helm_chart_values(self):
        return self.raw.get('helm_chart_values', None)

    def image_pull_secret(self):
        return self.snd.imagePullSecret

    def tls_secret_name(self):
        return self.snd.tls_secret_name

    def tls_config(self):
        return self.snd.tls_config

    def _required_attributes(self):
        return ['externalUrl', 'proxyUrl', 'teams', 'helm_chart_default_values_config']

    def _validate_dict(self):
        super()._validate_dict()
        # We check for the existence of the 'main'-team as it is the only team that is *required* to
        # exist for any concourse server.
        if not self.snd.teams['main']:
            raise ModelValidationError('No team "main" defined.')
        # implicitly validate main team
        self.team_credentials('main')


class ConcourseTeamCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def teamname(self):
        return self.snd.teamname

    def github_auth_team(self, split: bool=False):
        '''
        Returns a string in the form `organisation/team` representing the github-team whose members are able
        to login to this team using github-oauth.

        @param split: if `true` the function will return the organisation and team as a list with two elements,
        i.e. `(organization, team)`
        '''
        if split and self.snd.gitAuthTeam:
            return self.snd.gitAuthTeam.split('/')
        return self.snd.gitAuthTeam

    def github_auth_client_id(self):
        return self.snd.githubAuthClientId

    def github_auth_client_secret(self):
        return self.snd.githubAuthClientSecret

    def github_auth_auth_url(self):
        return self.snd.githubAuthAuthUrl

    def github_auth_token_url(self):
        return self.snd.githubAuthTokenUrl

    def github_auth_api_url(self):
        return self.snd.githubAuthApiUrl

    def has_basic_auth_credentials(self):
        if self.snd.username or self.snd.password:
            return True
        return False

    def has_github_oauth_credentials(self):
        if (
            self.snd.gitAuthTeam or
            self.snd.githubAuthClientId or
            self.snd.githubAuthClientSecret
        ):
            return True
        return False

    def has_custom_github_auth_urls(self):
        if (
          self.snd.githubAuthAuthUrl or
          self.snd.githubAuthTokenUrl or
          self.snd.githubAuthApiUrl
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
        return self.snd.host

    def smtp_port(self):
        return self.snd.port

    def credentials(self):
        return EmailCredentials(self.snd.technicalUser)

    def _required_attributes(self):
        return ['host', 'port', 'technicalUser']

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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def job_mappings(self):
        return [JobMapping(raw_dict=dict(raw)) for raw in self.raw]


class JobMapping(ModelBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def team_name(self)->str:
        return self.snd.concourse_team_name

    def definition_dirs(self):
        return self.raw['definition_dirs']

    def _required_attributes(self):
        return ['concourse_team_name', 'definition_dirs']

    def _validate_dict(self):
        super()._validate_dict()


class KubernetesConfig(NamedModelElement):
    def kubeconfig(self):
        return self.raw.get('kubeconfig')


class SecretsServerConfig(NamedModelElement):
    def namespace(self):
        return self.snd.namespace

    def service_name(self):
        return self.snd.service_name

    def endpoint_url(self):
        return 'http://{sn}.{ns}.svc.cluster.local'.format(
            sn=self.service_name(),
            ns=self.namespace(),
        )

    def secrets(self):
        return SecretsServerSecrets(raw_dict=self.raw['secrets'])


class SecretsServerSecrets(ModelBase):
    def concourse_secret_name(self):
        return self.snd.concourse_config.name

    def concourse_attribute(self):
        return self.snd.concourse_config.attribute

    def cfg_set_names(self):
        return self.raw['cfg_sets']


class TlsConfig(NamedModelElement):
    def private_key(self):
        return self.snd.private_key

    def certificate(self):
        return self.snd.certificate

    def _required_attributes(self):
        return ['private_key', 'certificate']
