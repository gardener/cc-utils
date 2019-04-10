# Copyright (c) 2019 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
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

from enum import Enum

from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelValidationError,
)


class ConcourseApiVersion(Enum):
    '''Enum to define different Concourse versions'''
    V4 = '4'


class ConcourseConfig(NamedModelElement):
    '''
    Not intended to be instantiated by users of this module
    '''

    def all_team_credentials(self):
        return [ConcourseTeamCredentials(team_dict) for team_dict in self.raw.get('teams').values()]

    def external_url(self):
        return self.raw.get('externalUrl')

    def job_mapping_cfg_name(self):
        return self.raw.get('job_mapping')

    def team_credentials(self, teamname):
        raw_credentials = self.raw.get('teams').get(teamname)
        if not raw_credentials:
            raise ValueError('unknown team {t}; known: {kt}'.format(
                t=teamname,
                kt=', '.join(self.raw.get('teams').keys()),
                )
            )
        return ConcourseTeamCredentials(raw_credentials)

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

    def kubernetes_cluster_config(self):
        return self.raw.get('kubernetes_cluster_config')

    def disable_github_pr_webhooks(self):
        '''
        If set to True, the rendered concourse pull-request resources don't have webhooks configured.
        This is because of problems using webhooks on our internal Github.
        '''
        return self.raw.get('disable_webhook_for_pr', False)

    def ingress_host(self):
        '''
        Returns the hostname added as additional ingress.
        '''
        return self.raw.get('ingress_host')

    def ingress_url(self):
        return 'https://' + self.ingress_host()

    def helm_chart_version(self):
        return self.raw.get('helm_chart_version')

    def concourse_version(self) -> ConcourseApiVersion:
        return ConcourseApiVersion(self.raw.get('concourse_version'))

    def github_enterprise_host(self):
        return self.raw.get('github_enterprise_host')

    def proxy(self):
        return self.raw.get('proxy')

    def _required_attributes(self):
        return [
            'externalUrl',
            'teams',
            'helm_chart_default_values_config',
            'kubernetes_cluster_config',
            'concourse_version',
            'job_mapping',
            'imagePullSecret',
            'tls_secret_name',
            'tls_config',
            'ingress_host',
            'helm_chart_version',
            'helm_chart_values',
        ]

    def _optional_attributes(self):
        return {
            'github_enterprise_host',
            'proxy',
        }

    def validate(self):
        super().validate()
        # We check for the existence of the 'main'-team as it is the only team that is *required* to
        # exist for any concourse server.
        if not self.raw.get('teams').get('main'):
            raise ModelValidationError('No team "main" defined.')
        # implicitly validate main team
        self.team_credentials('main')
        # Check for valid versions
        if self.concourse_version() not in ConcourseApiVersion:
            raise ModelValidationError(
                'Concourse version {v} not supported'.format(v=self.concourse_version())
            )


class ConcourseTeamCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''

    def teamname(self):
        return self.raw.get('teamname')

    def github_auth_team(self, split: bool=False):
        '''
        returns the github auth team (org:team)

        @param split: if `true` return [org, team]
        '''
        if split and self.raw.get('gitAuthTeam'):
            return self.raw.get('gitAuthTeam').split(':')
        return self.raw.get('gitAuthTeam')

    def github_auth_client_id(self):
        return self.raw.get('githubAuthClientId')

    def github_auth_client_secret(self):
        return self.raw.get('githubAuthClientSecret')

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

    def _required_attributes(self):
        _required_attributes = ['teamname']
        if self.has_basic_auth_credentials():
            _required_attributes.extend(['username', 'password'])
        if self.has_github_oauth_credentials():
            _required_attributes.extend(
                ['gitAuthTeam', 'githubAuthClientId', 'githubAuthClientSecret']
            )
        return _required_attributes

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
    def team_name(self)->str:
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
