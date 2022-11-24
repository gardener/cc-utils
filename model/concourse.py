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
from enum import Enum
import typing
import re
import urllib.parse

import dacite

import ci.util
import reutil

from . import cluster_domain_from_kubernetes_config

from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelValidationError,
    ModelBase,
)


class ConcourseOAuthConfig(NamedModelElement):
    def client_id(self) -> str:
        return self.raw['client_id']

    def client_secret(self) -> str:
        return self.raw['client_secret']

    def _required_attributes(self):
        return [
            'client_id',
            'client_secret'
        ]


@dataclasses.dataclass
class Platform:
    '''
    a slightly opinionated platform name, describing a combination of an operating system and
    hardware (/CPU) architecture.

    name: arbitrary, chosen name to reference the platform (agnostic of 3rd-parties, should not
          be exposed externally)
    oci_name: the name this platform is named in the context of OCI/Docker
    worker_tag: the tag (concourse) workers of this platform are marked with

    all values are _not_ intended for being parsed/interpreted (except by e.g. containerd when being
    passed oci_name).

    --
    see https://github.com/containerd/containerd/blob/v1.5.7/platforms/platforms.go#L63 for
    allowed/known platform names

    different than stated there, oci_names *must* always specify os and  architecture here.
    '''
    name: str
    oci_name: str
    worker_tag: str | None

    def matches_oci_platform_name(self, oci_platform_name: str):
        return Platform.normalise_oci_platform_name(self.oci_name) == \
            Platform.normalise_oci_platform_name(oci_platform_name)

    @property
    def normalised_oci_platform_name(self):
        return Platform.normalise_oci_platform_name(self.oci_name)

    @property
    def normalised_oci_platform_tag_suffix(self):
        return self.normalised_oci_platform_name.replace('/', '-')

    @staticmethod
    def normalise_oci_platform_name(name: str):
        osname, arch = name.split('/', 1) # we want to fail if no / present

        if arch == 'aarch64':
            arch = 'arm64'
        elif arch == 'armhf':
            arch = 'arm'
        elif arch == 'armel':
            arch = 'arm/v6'
        elif arch == 'i386':
            arch = '386'
        elif arch in ('x86_64', 'x86-64'):
            arch = 'amd64'

        return f'{osname}/{arch}'


@dataclasses.dataclass
class WorkerNodeConfig:
    '''
    concourse worker node configuration. For now, configuration only contains platform (os/arch).

    if not configured, all nodes are assumed to run on the same, implicit default platform
    '''
    default_platform_name: str = None
    platforms: typing.List[Platform] | None = None

    def platform_for_oci_platform(self, oci_platform_name: str, absent_ok=True):
        if absent_ok and not self.platforms:
            return None

        for platform in self.platforms:
            if platform.matches_oci_platform_name(oci_platform_name):
                return platform
        if absent_ok:
            return None
        raise ValueError(f'no platform for {oci_platform_name=} found')

    @property
    def default_platform(self):
        if not self.default_platform_name:
            return None

        for platform in self.platforms:
            if platform.name == self.default_platform_name:
                return platform

        raise ValueError('default platform name was configured, but not found in platforms')


class ConcourseConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def external_url(self):
        return self.raw.get('externalUrl')

    def job_mapping_cfg_name(self):
        return self.raw.get('job_mapping')

    def helm_chart_default_values_config(self):
        return self.raw.get('helm_chart_default_values_config')

    def helm_chart_values(self):
        return self.raw.get('helm_chart_values', None)

    def image_pull_secret(self):
        return self.raw.get('imagePullSecret')

    def tls_secret_name(self):
        return self.raw.get('tls_secret_name')

    def kubernetes_cluster_config(self):
        return self.raw.get('kubernetes_cluster_config')

    def clamav_config(self):
        return self.raw.get('clamav_config')

    def disable_github_pr_webhooks(self):
        '''
        If set to True, the rendered concourse pull-request resources don't have webhooks configured.
        This is because of problems using webhooks on our internal Github.
        '''
        return self.raw.get('disable_webhook_for_pr', False)

    def ingress_host(self, config_factory):
        cluster_domain = cluster_domain_from_kubernetes_config(
            config_factory,
            self.kubernetes_cluster_config(),
        )
        return f'concourse.{cluster_domain}'

    def ingress_config(self):
        return self.raw.get('ingress_config')

    def ingress_url(self, config_factory):
        return 'https://' + self.ingress_host(config_factory)

    def helm_chart_version(self):
        return self.raw.get('helm_chart_version')

    def github_enterprise_host(self):
        return self.raw.get('github_enterprise_host')

    def proxy(self):
        return self.raw.get('proxy')

    def deploy_storage_class(self):
        return self.raw.get('deploy_storage_class', False)

    def oauth_config_name(self):
        return self.raw['oauth_config_name']

    def concourse_endpoint_name(self):
        return self.raw.get('concourse_endpoint_name')

    @property
    def worker_node_cfg(self) -> WorkerNodeConfig:
        return dacite.from_dict(
            data_class=WorkerNodeConfig,
            data=self.raw.get('worker_node_cfg', {}),
        )

    def is_accessible_from(self, url: str) -> bool:
        if not (domain_rules := self.raw.get('domain_rules', [])):
            return True

        for dr in domain_rules:
            if (
                re.compile(dr['domain_regex']).fullmatch(url)
                and not dr['accessible_from']
            ):
                return False

        return True

    def _required_attributes(self):
        return [
            'externalUrl',
            'helm_chart_default_values_config',
            'kubernetes_cluster_config',
            'job_mapping',
            'imagePullSecret',
            'tls_secret_name',
            'ingress_config',
            'helm_chart_version',
            'helm_chart_values',
            'oauth_config_name',
            'concourse_endpoint_name',
        ]

    def _optional_attributes(self):
        return {
            'clamav_config',
            'concourse_version', # TODO: Remove
            'deploy_storage_class',
            'domain_rules',
            'github_enterprise_host',
            'ingress_host', # TODO: Remove
            'proxy',
            'worker_node_cfg',
        }

    def validate(self):
        super().validate()


class ConcourseEndpoint(NamedModelElement):
    def base_url(self) -> str:
        return self.raw.get('base_url')


class ConcourseTeamConfig(NamedModelElement):
    def concourse_endpoint_name(self) -> str:
        return self.raw['concourse_endpoint_name']

    def service_user(self) -> typing.Optional[BasicCredentials]:
        if local_user := self.raw.get('service_user'):
            return BasicCredentials(raw_dict=local_user)

    def team_name(self) -> str:
        return self.raw.get('team_name')

    def username(self) -> str:
        if service_user := self.service_user():
            return service_user.username()

    def password(self) -> str:
        if service_user := self.service_user():
            return service_user.passwd()

    def role(self) -> str:
        return self.raw.get('role')

    def github_auth_team(self, split: bool = False) -> typing.Optional[str]:
        """
        returns the github auth team (org:team)

        @param split: if `true` return [org, team]
        """
        git_auth_team = self.raw.get('git_auth_team')
        if split and git_auth_team:
            return git_auth_team.split(':')
        return git_auth_team

    def has_basic_auth_credentials(self) -> bool:
        if self.username() or self.password():
            return True
        return False

    def _required_attributes(self):
        return [
            'concourse_endpoint_name',
            'git_auth_team',
            'role',
            'service_user',
            'team_name',
        ]


def get_team_cfg_by_name(cc_team_cfgs, name: str) -> typing.Optional[ConcourseTeamConfig]:
    for cc_team_cfg in cc_team_cfgs:
        if cc_team_cfg.team_name() == name:
            return cc_team_cfg


def get_main_team_cfg(cc_team_cfgs):
    return get_team_cfg_by_name(cc_team_cfgs, 'main')


class ConcourseUAM(NamedModelElement):
    def local_user(self) -> typing.Optional[BasicCredentials]:
        if local_user := self.raw.get('local_user'):
            return BasicCredentials(raw_dict=local_user)

    def team_name(self) -> str:
        return self.raw.get('team_name')

    def username(self) -> str:
        if local_user := self.local_user():
            return local_user.username()

    def password(self) -> str:
        if local_user := self.local_user():
            return local_user.passwd()

    def role(self) -> str:
        return self.raw.get('role')

    def github_auth_team(self, split: bool=False) -> typing.Optional[str]:
        '''
        returns the github auth team (org:team)

        @param split: if `true` return [org, team]
        '''
        git_auth_team = self.raw.get('git_auth_team')
        if split and git_auth_team:
            return git_auth_team.split(':')
        return git_auth_team

    def has_basic_auth_credentials(self):
        if self.username() or self.password():
            return True
        return False

    def _required_attributes(self):
        return [
            'local_user',
            'role',
            'git_auth_team',
            'team_name',
        ]


class ConcourseTeam(NamedModelElement):
    def teamname(self):
        return self.name()

    def username(self):
        return self.raw.get('username')

    def password(self):
        return self.raw.get('password')

    def role(self):
        return self.raw.get('role')

    def github_auth_team(self, split: bool=False):
        '''
        returns the github auth team (org:team)

        @param split: if `true` return [org, team]
        '''
        if split and self.raw.get('git_auth_team'):
            return self.raw.get('git_auth_team').split(':')
        return self.raw.get('git_auth_team')

    def github_auth_client_id(self):
        return self.raw.get('github_auth_client_id')

    def github_auth_client_secret(self):
        return self.raw.get('github_auth_client_secret')

    def has_basic_auth_credentials(self):
        if self.raw.get('username') or self.raw.get('password'):
            return True
        return False

    def has_github_oauth_credentials(self):
        if (
            self.raw.get('git_auth_team') or
            self.raw.get('github_auth_client_id') or
            self.raw.get('github_auth_client_secret')
        ):
            return True
        return False

    def _required_attributes(self):
        yield from super()._required_attributes()
        if self.has_basic_auth_credentials():
            yield from ['username', 'password']
        if self.has_github_oauth_credentials():
            yield from ['git_auth_team', 'github_auth_client_id', 'github_auth_client_secret']

    def validate(self):
        super().validate()
        if self.has_github_oauth_credentials():
            github_org_and_team = self.github_auth_team(split=True)
            # explicitly check for expected structure, raise error if not found
            if github_org_and_team and len(github_org_and_team) == 2:
                github_org, github_team = github_org_and_team
                if github_org and github_team:
                    return
            raise ModelValidationError(
                'Invalid github-oauth team. Expected <org>/<team>, got {t}'.format(
                    t=github_org_and_team
                )
            )


class ConcourseUAMConfig(NamedModelElement):
    def teams(self) -> typing.List[ConcourseTeam]:
        return [
            ConcourseTeam(name=name, raw_dict=raw)
            for name, raw in self.raw.get('teams').items()
        ]

    def team(self, team_name: str):
        for team in self.teams():
            if team.teamname() == team_name:
                return team
        raise ValueError(
            f"Unknown team '{team_name}'; known teams: {', '.join(self.raw.get('teams').keys())}"
        )

    def main_team(self):
        return self.team('main')

    def _required_attributes(self):
        yield from super()._required_attributes()
        yield from [
            'teams',
        ]

    def validate(self):
        # We check for the existence of the 'main'-team as it is the only team that is *required* to
        # exist for any concourse server.
        if not self.main_team():
            raise ModelValidationError("No team 'main' defined.")
        # explicitly validate main team
        self.main_team().validate()


class PipelineCleanupPolicy(Enum):
    CLEANUP_EXTRA_PIPELINES = 'cleanup_extra_pipelines'
    NO_CLEANUP = 'no_cleanup'


class JobMappingSet(NamedModelElement):
    def job_mappings(self):
        return {name: JobMapping(name=name, raw_dict=raw) for name, raw in self.raw.items()}

    def job_mapping_for_repo_url(self, repo_url: str, cfg_set):
        for _, job_mapping in self.job_mappings().items():
            if job_mapping.matches_repo_url(repo_url, cfg_set):
                return job_mapping

        raise ValueError(f'no matching job mapping for {repo_url=}')

    def job_mapping_for_team_name(self, team_name: str):
        for _, job_mapping in self.job_mappings().items():
            if job_mapping.concourse_target_team() == team_name:
                return job_mapping

        raise ValueError(f'no matching job mapping for {team_name=}')

    def __iter__(self):
        return self.job_mappings().values().__iter__()

    def __getitem__(self, name: str):
        return JobMapping(name=name, raw_dict=self.raw.__getitem__(name))

    def validate(self):
        tgt_team_names = {jm.team_name() for jm in self}
        for team_name in (jm.team_name() for jm in self):
            if not team_name in tgt_team_names:
                raise ModelValidationError(f'{team_name=} must only be specified once')
            tgt_team_names.remove(team_name)


class SecretNamePattern(Enum):
    PREFIX = 'cc-'
    POSTFIX = '-config'


class SecretsRepo(ModelBase):
    def github_cfg(self):
        return self.raw.get('github_cfg')

    def org(self):
        return self.raw.get('org')

    def repo(self):
        return self.raw.get('repo')


def cfg_name_from_team(team_name):
    team_name = re.sub('(.)([A-Z][a-z]+)', r'\1-\2', team_name)
    team_name = re.sub('([a-z0-9])([A-Z])', r'\1-\2', team_name).lower()
    return f'{SecretNamePattern.PREFIX.value}{team_name}{SecretNamePattern.POSTFIX.value}'


def secret_cfg_name_for_team(team_name):
    return f'{team_name}_cfg'


def secret_name_from_team(team_name: str, key_generation: int) -> str:
    if key_generation is None:
        return cfg_name_from_team(team_name)
    else:
        return f'{cfg_name_from_team(team_name)}-{key_generation}'


class JobMapping(NamedModelElement):
    def team_name(self) -> str:
        # todo: use `name` attr for that (thus enforce unique mappings)
        return self.raw.get('concourse_target_team')

    def cleanup_policy(self) -> PipelineCleanupPolicy:
        return PipelineCleanupPolicy(
            self.raw.get('cleanup_policy', PipelineCleanupPolicy.CLEANUP_EXTRA_PIPELINES)
        )

    def replication_ctx_cfg_set(self) -> str:
        return self.raw.get('replication_ctx_cfg_set')

    def github_organisations(self):
        return [
            GithubOrganisationConfig(name, raw)
            for name, raw in self.raw.get('github_orgs', {}).items()
        ]

    def secret_cfg(self) -> str:
        return self.raw.get('secret_cfg')

    def secrets_repo(self) -> typing.Optional[SecretsRepo]:
        if secrets_repo := self.raw.get('secrets_repo'):
            return SecretsRepo(secrets_repo)

    def concourse_team_cfg_name(self) -> str:
        return self.raw.get('concourse_team_cfg_name')

    def matches_repo_url(self, repo_url, cfg_set) -> bool:
        repo_url = ci.util.urlparse(repo_url)
        org, repo = repo_url.path[1:].split('/')

        for github_org_cfg in self.github_organisations():
            if not github_org_cfg.org_name() == org:
                continue

            gh_cfg = cfg_set.github(github_org_cfg.github_cfg_name())
            if not gh_cfg.matches_repo_url(urllib.parse.urlunparse(repo_url)):
                continue

            if github_org_cfg.repository_matches(repo):
                return True

        return False

    def secrets_replication_pipeline_target_cc_team_cfg_name(self) -> typing.Optional[str]:
        '''
            when set the secrets replication pipline will be rendered into the defined team
        '''
        return self.raw.get('secrets_replication_pipeline_target_cc_team_cfg_name')

    def unpause_new_pipelines(self) -> bool:
        '''Whether newly created pipelines are to be unpaused.
        '''
        return self.raw.get('unpause_new_pipelines', True)

    def unpause_deployed_pipelines(self) -> bool:
        '''Whether deployed pipelines are to be unpaused unconditionally.
        '''
        return self.raw.get('unpause_deployed_pipelines', False)

    def expose_deployed_pipelines(self) -> bool:
        '''Whether to expose pipelines after deploying them.

        Exposed pipelines are viewable by authenticated users from other teams. Note: this does
        not grant access to buildlogs and build metadata.
        '''
        return self.raw.get('expose_pipelines', True)

    def trusted_teams(self) -> typing.Iterable[str]:
        '''Pull requests created/synchronized by members of this team will automatically have
        the required label set by the webhook-dispatcher
        '''
        return self.raw.get('trusted_teams', [])

    def compliance_reporting_repo_url(self) -> str:
        '''Target repo for compliance issues based on cfg policy violations.

        If not set, reporting will still be active, just the issue generation will be disabled.
        '''
        return self.raw.get('compliance_reporting_repo_url', None)

    def _required_attributes(self):
        return [
            'concourse_target_team',
            'replication_ctx_cfg_set',
        ]

    def _optional_attributes(self):
        return [
            'compliance_reporting_repo_url',
            'concourse_team_cfg_name',
            'expose_pipelines',
            'secret_cfg',
            'secrets_replication_pipeline_target_cc_team_cfg_name',
            'secrets_repo',
            'trusted_teams',
            'unpause_deployed_pipelines',
            'unpause_new_pipelines',
        ]

    def _defaults_dict(self):
        # XXX find out why this is not honoured
        return {
            'cleanup_policy': PipelineCleanupPolicy.CLEANUP_EXTRA_PIPELINES,
        }

    def target_secret_name(self):
        '''
            k8s secret name used for replication
        '''
        return cfg_name_from_team(self.team_name())

    def target_secret_cfg_name(self):
        '''
            name of the config in the target k8s secret
        '''
        return secret_cfg_name_for_team(self.team_name())


class GithubOrganisationConfig(NamedModelElement):
    def github_cfg_name(self):
        return self.raw.get('github_cfg')

    def org_name(self):
        return self.raw.get('github_org')

    def include_repositories(self):
        return self.raw.get('include_repositories', ())

    def exclude_repositories(self):
        return self.raw.get('exclude_repositories', ())

    def repository_matches(self, repository_name: str):
        repo_filter = reutil.re_filter(
            include_regexes=self.include_repositories(),
            exclude_regexes=self.exclude_repositories(),
        )

        return repo_filter(repository_name)
