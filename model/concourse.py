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

import urllib.parse

from enum import Enum

import requests
import requests.exceptions
import reutil
import semver

import ci.util
import http_requests

from . import cluster_domain_from_kubernetes_config

from model.base import (
    NamedModelElement,
    ModelValidationError,
)


class ConcourseApiVersion(Enum):
    '''Enum to define different Concourse versions'''
    V5 = '5.0.0'
    V6_3_0 = '6.3.0'
    V6_5_1 = '6.5.1'


CONCOURSE_INFO_API_ENDPOINT = 'api/v1/info'
CONCOURSE_SUBDOMAIN_LABEL = 'concourse'


class ConcourseConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def external_url(self):
        return self.raw.get('externalUrl')

    def job_mapping_cfg_name(self):
        return self.raw.get('job_mapping')

    def concourse_uam_config(self):
        return self.raw.get('concourse_uam_config')

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
        return f'{CONCOURSE_SUBDOMAIN_LABEL}.{cluster_domain}'

    def ingress_config(self):
        return self.raw.get('ingress_config')

    def ingress_url(self, config_factory):
        return 'https://' + self.ingress_host(config_factory)

    def helm_chart_version(self):
        return self.raw.get('helm_chart_version')

    def _concourse_version(self, config_factory):
        session = requests.Session()
        http_requests.mount_default_adapter(session)
        concourse_url = urllib.parse.urljoin(
            self.ingress_url(config_factory),
            CONCOURSE_INFO_API_ENDPOINT,
        )
        try:
            response = session.get(concourse_url)
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            ci.util.warning(f'Could not determine version of Concourse running at {concourse_url}')
            return None

        return response.json()['version']

    def compatible_api_version(self, config_factory) -> ConcourseApiVersion:
        # query concourse for its version. If that fails, use the greatest known version instead.
        if not (parsed_cc_version := self._concourse_version(config_factory)):
            parsed_cc_version = max(
                [v.value for v in ConcourseApiVersion],
                key=lambda x:semver.VersionInfo.parse(x)
            )
            ci.util.info(f"Defaulting to version '{parsed_cc_version}'")

        if parsed_cc_version >= semver.VersionInfo.parse('6.5.1'):
            return ConcourseApiVersion.V6_5_1
        if parsed_cc_version >= semver.VersionInfo.parse('6.3.0'):
            return ConcourseApiVersion.V6_3_0
        elif parsed_cc_version >= semver.VersionInfo.parse('5.0.0'):
            return ConcourseApiVersion.V5
        else:
            raise NotImplementedError

    def github_enterprise_host(self):
        return self.raw.get('github_enterprise_host')

    def proxy(self):
        return self.raw.get('proxy')

    def _required_attributes(self):
        return [
            'externalUrl',
            'concourse_uam_config',
            'helm_chart_default_values_config',
            'kubernetes_cluster_config',
            'job_mapping',
            'imagePullSecret',
            'tls_secret_name',
            'ingress_config',
            'helm_chart_version',
            'helm_chart_values',
        ]

    def _optional_attributes(self):
        return {
            'clamav_config',
            'concourse_version', # TODO: Remove
            'github_enterprise_host',
            'ingress_host', # TODO: Remove
            'proxy',
        }

    def validate(self):
        super().validate()


class ConcourseUAMConfig(NamedModelElement):
    def teams(self):
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


class JobMappingSet(NamedModelElement):
    def job_mappings(self):
        return {name: JobMapping(name=name, raw_dict=raw) for name, raw in self.raw.items()}


class JobMapping(NamedModelElement):
    def team_name(self) -> str:
        return self.raw.get('concourse_target_team')

    def github_organisations(self):
        return [
            GithubOrganisationConfig(name, raw)
            for name, raw in self.raw.get('github_orgs').items()
        ]

    def _required_attributes(self):
        return ['concourse_target_team']


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
